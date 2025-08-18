import logging
import time
from typing import List, Dict, Set, Optional
from django.db import transaction, IntegrityError
from django.conf import settings

import discogs_client

from records.models import Record, RecordConditions
from records.services import RecordService, DiscogsService, ImageService

logger = logging.getLogger(__name__)


class CollectionSyncService:
    """Сервис синхронизации коллекции пользователя из Discogs.

    Основные принципы:
    1. discogs_id - уникальный идентификатор записи
    2. Записи из коллекции должны иметь stock=1
    3. Записи не из коллекции должны иметь stock=0
    4. Дубликаты в коллекции Discogs игнорируются
    """

    def __init__(self):
        """Инициализация сервиса."""
        self.client = discogs_client.Client(
            settings.DISCOGS_USER_AGENT,
            user_token=settings.DISCOGS_TOKEN
        )
        self.record_service = RecordService(
            discogs_service=DiscogsService(),
            image_service=ImageService()
        )
        self.discogs_service = DiscogsService()

    def sync_user_collection(self, username: str, update_stock: bool = True) -> Dict[str, any]:
        """Синхронизация коллекции пользователя.

        Args:
            username: Имя пользователя Discogs.
            update_stock: Обновлять ли остатки для записей вне коллекции.

        Returns:
            Словарь с результатами синхронизации.
        """
        logger.info(f"Starting collection sync for user: {username}")
        start_time = time.time()

        # Получаем уникальные ID из коллекции Discogs
        try:
            discogs_ids_list = self._get_collection_discogs_ids(username)
            collection_set = set(discogs_ids_list)  # Убираем дубликаты
        except Exception as e:
            logger.error(f"Failed to get collection for {username}: {e}")
            raise

        logger.info(
            f"Found {len(discogs_ids_list)} items "
            f"({len(collection_set)} unique) in {username}'s collection"
        )

        # Получаем все записи из БД с discogs_id одним запросом
        db_records = Record.objects.filter(
            discogs_id__isnull=False
        ).values('id', 'discogs_id', 'stock')

        # Создаем словари для быстрого доступа
        db_by_discogs = {r['discogs_id']: r for r in db_records}
        db_discogs_ids = set(db_by_discogs.keys())

        logger.info(f"Found {len(db_discogs_ids)} records with discogs_id in database")

        # Определяем что нужно сделать
        to_add = collection_set - db_discogs_ids  # Нужно добавить
        in_both = collection_set & db_discogs_ids  # Есть и там и там
        only_in_db = db_discogs_ids - collection_set  # Только в БД

        # Результаты
        results = {
            'total_in_collection': len(collection_set),
            'added': 0,
            'restocked': 0,
            'already_in_stock': 0,
            'out_of_stock': 0,
            'errors': 0,
            'error_details': []
        }

        # 1. Обновляем stock для существующих записей (batch операция)
        if in_both:
            results['restocked'] = self._update_existing_records_stock(
                in_both, db_by_discogs
            )
            results['already_in_stock'] = len(in_both) - results['restocked']

        # 2. Снимаем с наличия записи вне коллекции (batch операция)
        if update_stock and only_in_db:
            results['out_of_stock'] = self._remove_from_stock(
                only_in_db, db_by_discogs
            )

        # 3. Добавляем новые записи
        if to_add:
            add_results = self._add_new_records(to_add)
            results['added'] = add_results['added']
            results['errors'] = add_results['errors']
            results['error_details'] = add_results['error_details']

        # Логируем результаты
        elapsed_time = time.time() - start_time
        logger.info(
            f"Sync completed in {elapsed_time:.2f}s - "
            f"Added: {results['added']}, "
            f"Restocked: {results['restocked']}, "
            f"Already in stock: {results['already_in_stock']}, "
            f"Out of stock: {results['out_of_stock']}, "
            f"Errors: {results['errors']}"
        )

        return results

    def _get_collection_discogs_ids(self, username: str) -> List[int]:
        """Получение списка Discogs ID из коллекции пользователя.

        Returns:
            Список Discogs ID (могут быть дубликаты).
        """
        user = self.client.user(username)

        folders = user.collection_folders
        if not folders:
            raise Exception("Collection is empty or not accessible")

        # Берем основную папку (All)
        main_folder = folders[0]
        logger.info(f"Processing folder: {main_folder.name}")

        discogs_ids = []
        releases = main_folder.releases

        # Обработка с пагинацией
        if hasattr(releases, 'page'):
            page = 1
            while True:
                try:
                    releases_page = releases.page(page)

                    for item in releases_page:
                        if hasattr(item, 'release'):
                            discogs_ids.append(item.release.id)
                        else:
                            discogs_ids.append(item.id)

                    if hasattr(releases_page, 'pages') and page >= releases_page.pages:
                        break

                    if len(releases_page) == 0:
                        break

                    page += 1
                    if page % 10 == 0:
                        logger.info(f"Processed {page} pages...")

                except Exception as e:
                    logger.debug(f"Finished processing pages: {e}")
                    break
        else:
            # Простой итератор
            for item in releases:
                try:
                    if hasattr(item, 'release'):
                        discogs_ids.append(item.release.id)
                    elif hasattr(item, 'id'):
                        discogs_ids.append(item.id)

                    if len(discogs_ids) % 100 == 0:
                        logger.info(f"Processed {len(discogs_ids)} items...")

                except Exception as e:
                    logger.warning(f"Error processing item: {e}")
                    continue

        return discogs_ids

    def _update_existing_records_stock(
            self,
            discogs_ids: Set[int],
            db_records_dict: Dict[int, dict]
    ) -> int:
        """Обновление stock для существующих записей.

        Returns:
            Количество обновленных записей.
        """
        # Находим записи с stock=0 которые нужно вернуть в наличие
        to_restock_ids = [
            db_records_dict[did]['id']
            for did in discogs_ids
            if db_records_dict[did]['stock'] == 0
        ]

        if to_restock_ids:
            with transaction.atomic():
                updated = Record.objects.filter(
                    id__in=to_restock_ids
                ).update(stock=1)
                logger.info(f"Restocked {updated} records (stock: 0 → 1)")
                return updated

        return 0

    def _remove_from_stock(
            self,
            discogs_ids: Set[int],
            db_records_dict: Dict[int, dict]
    ) -> int:
        """Снятие с наличия записей вне коллекции.

        Returns:
            Количество обновленных записей.
        """
        # Находим записи с stock>0 которые нужно снять с наличия
        to_remove_ids = [
            db_records_dict[did]['id']
            for did in discogs_ids
            if db_records_dict[did]['stock'] > 0
        ]

        if to_remove_ids:
            with transaction.atomic():
                updated = Record.objects.filter(
                    id__in=to_remove_ids
                ).update(stock=0)
                logger.info(f"Removed {updated} records from stock (stock → 0)")
                return updated

        return 0

    def _add_new_records(self, discogs_ids: Set[int]) -> Dict[str, any]:
        """Добавление новых записей из Discogs.

        Returns:
            Словарь с количеством добавленных записей и ошибок.
        """
        results = {
            'added': 0,
            'errors': 0,
            'error_details': []
        }

        # Сортируем для стабильности
        sorted_ids = sorted(discogs_ids)

        logger.info(f"Adding {len(sorted_ids)} new records...")

        for i, discogs_id in enumerate(sorted_ids, 1):
            # Прогресс
            if i % 10 == 0:
                logger.info(f"Progress: {i}/{len(sorted_ids)}")

            try:
                # Задержка для rate limiting
                time.sleep(0.5)

                # Создаем запись
                record = self._create_record_from_discogs(discogs_id)
                if record:
                    results['added'] += 1
                    logger.info(
                        f"Added record: {record.title} "
                        f"(ID={record.id}, Discogs={discogs_id})"
                    )

            except Exception as e:
                logger.error(f"Failed to add discogs_id={discogs_id}: {e}")
                results['errors'] += 1
                results['error_details'].append({
                    'discogs_id': discogs_id,
                    'error': str(e)
                })

        return results

    def _create_record_from_discogs(self, discogs_id: int) -> Optional[Record]:
        """Создание записи из Discogs.

        Args:
            discogs_id: ID релиза в Discogs.

        Returns:
            Созданная запись или None при ошибке.
        """
        # Еще раз проверяем что записи нет (race condition)
        existing = Record.objects.filter(discogs_id=discogs_id).first()
        if existing:
            logger.debug(f"Record already exists for discogs_id={discogs_id}")
            if existing.stock == 0:
                existing.stock = 1
                existing.save()
            return existing

        # Получаем данные из Discogs
        release = self.client.release(discogs_id)
        if not release:
            raise ValueError(f"Release {discogs_id} not found in Discogs")

        # Извлекаем данные
        record_data = self.discogs_service.extract_release_data(release)

        # Валидируем barcode
        if record_data.get('barcode'):
            record_data['barcode'] = self._validate_barcode(record_data['barcode'])

        # Проверяем конфликты уникальности
        record_data = self._handle_uniqueness_conflicts(record_data)

        # Создаем запись
        with transaction.atomic():
            record = Record.objects.create(
                title=record_data['title'],
                discogs_id=record_data['discogs_id'],
                release_year=record_data.get('year'),
                catalog_number=record_data.get('catalog_number'),
                barcode=record_data.get('barcode'),
                country=record_data.get('country'),
                notes=record_data.get('notes'),
                condition=RecordConditions.M,
                stock=1  # Всегда 1 для записей из коллекции
            )

            logger.debug(
                f"Created record {record.id}: "
                f"catalog={record.catalog_number}, "
                f"barcode={record.barcode}"
            )

            # Создаем связи и треки
            try:
                self.record_service._create_record_relations(record, release)
                self.record_service._create_tracks(record, release)

                # Загружаем обложку
                if release.images:
                    self.record_service.image_service.download_cover(
                        record, release.images[0]['uri']
                    )
            except Exception as e:
                logger.warning(f"Failed to create relations/cover: {e}")

            return record

    def _validate_barcode(self, barcode: str) -> Optional[str]:
        """Валидация и очистка barcode.

        Args:
            barcode: Исходный barcode.

        Returns:
            Валидный barcode или None.
        """
        if not barcode:
            return None

        # Очищаем от пробелов и дефисов
        clean_barcode = str(barcode).strip().replace(' ', '').replace('-', '')

        # Проверяем что только цифры
        if not clean_barcode.isdigit():
            logger.debug(f"Invalid barcode (not digits): {barcode}")
            return None

        # Проверяем длину (8-20 символов)
        if len(clean_barcode) < 8 or len(clean_barcode) > 20:
            logger.debug(f"Invalid barcode length ({len(clean_barcode)}): {barcode}")
            return None

        return clean_barcode

    def _handle_uniqueness_conflicts(self, record_data: dict) -> dict:
        """Обработка конфликтов уникальности.

        Если catalog_number или barcode уже существуют у другой записи,
        очищаем их для новой записи.

        Args:
            record_data: Данные записи.

        Returns:
            Обработанные данные записи.
        """
        # Проверяем catalog_number
        if record_data.get('catalog_number'):
            existing = Record.objects.filter(
                catalog_number=record_data['catalog_number']
            ).exclude(
                discogs_id=record_data['discogs_id']
            ).first()

            if existing:
                logger.warning(
                    f"Catalog number '{record_data['catalog_number']}' "
                    f"already exists (record {existing.id}). "
                    f"Creating without it for discogs_id={record_data['discogs_id']}"
                )
                record_data['catalog_number'] = None

        # Проверяем barcode
        if record_data.get('barcode'):
            existing = Record.objects.filter(
                barcode=record_data['barcode']
            ).exclude(
                discogs_id=record_data['discogs_id']
            ).first()

            if existing:
                logger.warning(
                    f"Barcode '{record_data['barcode']}' "
                    f"already exists (record {existing.id}). "
                    f"Creating without it for discogs_id={record_data['discogs_id']}"
                )
                record_data['barcode'] = None

        return record_data

    def verify_sync_status(self, username: str) -> Dict[str, any]:
        """Проверка статуса синхронизации без изменений.

        Args:
            username: Имя пользователя Discogs.

        Returns:
            Словарь со статусом и найденными проблемами.
        """
        # Получаем коллекцию
        discogs_ids = self._get_collection_discogs_ids(username)
        collection_set = set(discogs_ids)

        # Получаем записи из БД
        db_records = Record.objects.filter(
            discogs_id__isnull=False
        ).values('id', 'discogs_id', 'stock', 'title')

        # Анализируем проблемы
        issues = []

        for record in db_records:
            discogs_id = record['discogs_id']
            stock = record['stock']

            # Должно быть в наличии, но stock=0
            if discogs_id in collection_set and stock == 0:
                issues.append({
                    'type': 'should_be_in_stock',
                    'id': record['id'],
                    'discogs_id': discogs_id,
                    'title': record['title'][:50],
                    'current_stock': 0,
                    'expected_stock': 1
                })

            # Не должно быть в наличии, но stock>0
            elif discogs_id not in collection_set and stock > 0:
                issues.append({
                    'type': 'should_be_out_of_stock',
                    'id': record['id'],
                    'discogs_id': discogs_id,
                    'title': record['title'][:50],
                    'current_stock': stock,
                    'expected_stock': 0
                })

        return {
            'collection_size': len(collection_set),
            'db_records': len(list(db_records)),
            'issues_found': len(issues),
            'issues': issues
        }
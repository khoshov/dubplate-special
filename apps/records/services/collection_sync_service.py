import logging
from typing import List, Dict, Tuple
from django.db import transaction
from django.conf import settings

import discogs_client

from records.models import Record
from records.services import RecordService, DiscogsService, ImageService

logger = logging.getLogger(__name__)


class CollectionSyncService:
    """Сервис синхронизации коллекции пользователя из Discogs.

    Получает список релизов из коллекции пользователя Discogs
    и синхронизирует их с локальной базой данных.
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

    def sync_user_collection(self, username: str, update_stock: bool = True) -> Dict[str, any]:
        """Синхронизация коллекции пользователя.

        Args:
            username: Имя пользователя Discogs.
            update_stock: Обновлять ли остатки (ставить 0 для отсутствующих).

        Returns:
            Словарь с результатами синхронизации:
            - total_in_collection: Всего в коллекции Discogs
            - added: Количество добавленных записей
            - updated: Количество обновленных записей
            - errors: Количество ошибок
            - out_of_stock: Количество записей с обнуленными остатками
        """
        logger.info(f"Starting collection sync for user: {username}")

        # Получаем список ID из коллекции
        try:
            discogs_ids = self._get_collection_discogs_ids(username)
        except Exception as e:
            logger.error(f"Failed to get collection for {username}: {e}")
            raise

        logger.info(f"Found {len(discogs_ids)} releases in {username}'s collection")

        # Синхронизируем записи
        results = {
            'total_in_collection': len(discogs_ids),
            'added': 0,
            'updated': 0,
            'errors': 0,
            'out_of_stock': 0,
            'details': []
        }

        with transaction.atomic():
            # 1. Добавляем/обновляем записи из коллекции
            for discogs_id in discogs_ids:
                result = self._process_release(discogs_id)
                results['details'].append(result)

                if result['status'] == 'added':
                    results['added'] += 1
                elif result['status'] == 'updated':
                    results['updated'] += 1
                elif result['status'] == 'error':
                    results['errors'] += 1

            # 2. Обнуляем остатки для записей, которых нет в коллекции
            if update_stock:
                out_of_stock_count = self._update_out_of_stock_records(discogs_ids)
                results['out_of_stock'] = out_of_stock_count

        logger.info(
            f"Sync completed - Added: {results['added']}, "
            f"Updated: {results['updated']}, "
            f"Errors: {results['errors']}, "
            f"Out of stock: {results['out_of_stock']}"
        )

        return results

    """Исправленный метод _get_collection_discogs_ids в CollectionSyncService"""

    def _get_collection_discogs_ids(self, username: str) -> List[int]:
        """Получение списка Discogs ID из коллекции пользователя.

        Args:
            username: Имя пользователя Discogs.

        Returns:
            Список Discogs ID.

        Raises:
            Exception: При ошибке доступа к коллекции.
        """
        user = self.client.user(username)

        # Получаем папки коллекции
        folders = user.collection_folders
        if not folders:
            raise Exception("Collection is empty or not accessible")

        # Берем основную папку (All)
        main_folder = folders[0]
        logger.info(f"Processing folder: {main_folder.name} (ID: {main_folder.id})")

        discogs_ids = []

        # Проверяем тип объекта releases
        releases = main_folder.releases

        # Если это пагинируемый объект
        if hasattr(releases, 'page'):
            page = 1

            while True:
                try:
                    releases_page = releases.page(page)

                    for item in releases_page:
                        # Извлекаем ID релиза
                        if hasattr(item, 'release'):
                            discogs_id = item.release.id
                        else:
                            discogs_id = item.id

                        if discogs_id not in discogs_ids:
                            discogs_ids.append(discogs_id)

                    # Проверяем, есть ли еще страницы
                    if hasattr(releases_page, 'pages') and page >= releases_page.pages:
                        break

                    # Если нет информации о страницах, проверяем по количеству элементов
                    if len(releases_page) == 0:
                        break

                    page += 1
                    logger.debug(f"Processed page {page}")

                except Exception as e:
                    logger.debug(f"No more pages or error: {e}")
                    break

        # Если это обычный список или итератор
        else:
            for item in releases:
                try:
                    # Извлекаем ID релиза
                    if hasattr(item, 'release'):
                        discogs_id = item.release.id
                    elif hasattr(item, 'id'):
                        discogs_id = item.id
                    else:
                        logger.warning(f"Cannot extract ID from item: {type(item)}")
                        continue

                    if discogs_id not in discogs_ids:
                        discogs_ids.append(discogs_id)

                    # Для больших коллекций показываем прогресс
                    if len(discogs_ids) % 100 == 0:
                        logger.info(f"Processed {len(discogs_ids)} releases...")

                except Exception as e:
                    logger.warning(f"Error processing release item: {e}")
                    continue

        logger.info(f"Total releases found: {len(discogs_ids)}")

        return discogs_ids
    def _process_release(self, discogs_id: int) -> Dict[str, any]:
        """Обработка одного релиза.

        Args:
            discogs_id: ID релиза в Discogs.

        Returns:
            Словарь с результатом обработки.
        """
        try:
            # Проверяем, есть ли уже такая запись
            existing = Record.objects.find_by_discogs_id(discogs_id)

            if existing:
                # Обновляем существующую запись
                if existing.stock == 0:
                    existing.stock = 1
                    existing.save()
                    logger.info(f"Updated stock for existing record: {existing.id}")
                    return {
                        'discogs_id': discogs_id,
                        'status': 'updated',
                        'record_id': existing.id,
                        'title': existing.title
                    }
                else:
                    return {
                        'discogs_id': discogs_id,
                        'status': 'exists',
                        'record_id': existing.id,
                        'title': existing.title
                    }

            # Получаем данные из Discogs
            release = self.client.release(discogs_id)
            if not release:
                raise Exception(f"Release {discogs_id} not found in Discogs")

            # Создаем новую запись
            record = self._create_record_from_release(release)

            logger.info(f"Added new record: {record.id} - {record.title}")
            return {
                'discogs_id': discogs_id,
                'status': 'added',
                'record_id': record.id,
                'title': record.title
            }

        except Exception as e:
            logger.error(f"Failed to process release {discogs_id}: {e}")
            return {
                'discogs_id': discogs_id,
                'status': 'error',
                'error': str(e)
            }

    def _create_record_from_release(self, release) -> Record:
        """Создание записи из релиза Discogs.

        Args:
            release: Объект релиза из Discogs.

        Returns:
            Созданная запись.
        """
        # Используем record_service для создания
        # Сначала пытаемся найти barcode или catalog_number
        barcode = None
        catalog_number = None

        # Извлекаем идентификаторы
        if hasattr(release, 'identifiers'):
            for identifier in release.identifiers:
                if identifier.type == 'Barcode' and identifier.value:
                    barcode = identifier.value
                    break

        if hasattr(release, 'labels') and release.labels:
            catalog_number = release.labels[0].catno

        # Если есть идентификаторы, используем import_from_discogs
        if barcode or catalog_number:
            record, _ = self.record_service.import_from_discogs(
                barcode=barcode,
                catalog_number=catalog_number
            )
            return record

        # Иначе создаем напрямую
        from records.models import RecordConditions

        # Извлекаем данные через DiscogsService
        discogs_service = DiscogsService()
        record_data = discogs_service.extract_release_data(release)

        # Создаем запись
        record = Record.objects.create(
            title=record_data['title'],
            discogs_id=record_data['discogs_id'],
            release_year=record_data.get('year'),
            catalog_number=record_data.get('catalog_number'),
            barcode=record_data.get('barcode'),
            country=record_data.get('country'),
            notes=record_data.get('notes'),
            condition=RecordConditions.M,
            stock=1
        )

        # Создаем связи через record_service
        self.record_service._create_record_relations(record, release)
        self.record_service._create_tracks(record, release)

        # Загружаем обложку
        if release.images:
            self.record_service.image_service.download_cover(
                record, release.images[0]['uri']
            )

        return record

    def _update_out_of_stock_records(self, collection_discogs_ids: List[int]) -> int:
        """Обнуление остатков для записей, отсутствующих в коллекции.

        Args:
            collection_discogs_ids: Список ID из коллекции Discogs.

        Returns:
            Количество обновленных записей.
        """
        # Находим записи с discogs_id, которых нет в коллекции
        out_of_stock_records = Record.objects.filter(
            discogs_id__isnull=False,
            stock__gt=0
        ).exclude(
            discogs_id__in=collection_discogs_ids
        )

        count = out_of_stock_records.count()

        # Обнуляем остатки
        out_of_stock_records.update(stock=0)

        logger.info(f"Set stock to 0 for {count} records not in collection")

        return count
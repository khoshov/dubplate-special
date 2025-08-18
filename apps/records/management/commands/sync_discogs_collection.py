from django.core.management.base import BaseCommand
from django.utils import timezone

from records.models import Record
from records.services.collection_sync_service import CollectionSyncService


class Command(BaseCommand):
    """Команда для синхронизации коллекции пользователя из Discogs.

    Логика:
    - Если пластинка есть в коллекции Discogs → stock = 1
    - Если пластинки нет в коллекции Discogs → stock = 0
    - Новые пластинки добавляются с stock = 1
    - discogs_id - уникальный идентификатор
    """

    help = 'Синхронизирует коллекцию пользователя из Discogs с локальной базой данных'

    def add_arguments(self, parser):
        parser.add_argument(
            'username',
            type=str,
            help='Имя пользователя Discogs'
        )

        parser.add_argument(
            '--no-update-stock',
            action='store_true',
            help='Не обнулять остатки для записей вне коллекции'
        )

        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет сделано без выполнения изменений'
        )

    def handle(self, *args, **options):
        username = options['username']
        update_stock = not options['no_update_stock']
        dry_run = options['dry_run']

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(f"СИНХРОНИЗАЦИЯ КОЛЛЕКЦИИ DISCOGS")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Пользователь: {username}")
        self.stdout.write(f"Обновлять stock: {'Да' if update_stock else 'Нет'}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️  DRY RUN - изменения не будут применены\n"))

        sync_service = CollectionSyncService()

        try:
            start_time = timezone.now()

            if dry_run:
                self._dry_run_analysis(sync_service, username, update_stock)
            else:
                self._perform_sync(sync_service, username, update_stock)

            # Время выполнения
            duration = timezone.now() - start_time
            self.stdout.write(f"\n⏱️  Время выполнения: {duration}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n❌ Ошибка: {str(e)}"))
            raise

    def _dry_run_analysis(self, sync_service, username, update_stock):
        """Анализ без выполнения изменений."""
        self.stdout.write("Анализ коллекции...")

        # Получаем данные
        discogs_ids = sync_service._get_collection_discogs_ids(username)
        collection_set = set(discogs_ids)

        # Анализируем БД
        db_records = Record.objects.filter(discogs_id__isnull=False)
        db_discogs_ids = set(db_records.values_list('discogs_id', flat=True))

        # Вычисляем изменения
        to_add = collection_set - db_discogs_ids
        in_both = collection_set & db_discogs_ids
        only_in_db = db_discogs_ids - collection_set

        # Детальный анализ
        to_restock = db_records.filter(
            discogs_id__in=in_both,
            stock=0
        ).count()

        already_in_stock = db_records.filter(
            discogs_id__in=in_both,
            stock__gt=0
        ).count()

        to_remove_from_stock = 0
        if update_stock:
            to_remove_from_stock = db_records.filter(
                discogs_id__in=only_in_db,
                stock__gt=0
            ).count()

        # Вывод результатов
        self.stdout.write("\n📊 РЕЗУЛЬТАТЫ АНАЛИЗА:")
        self.stdout.write("-" * 40)

        self.stdout.write(f"Записей в коллекции Discogs: {len(discogs_ids)}")
        self.stdout.write(f"Уникальных в коллекции: {len(collection_set)}")
        self.stdout.write(f"Записей в БД с discogs_id: {len(db_discogs_ids)}")

        self.stdout.write("\n📝 ПЛАНИРУЕМЫЕ ИЗМЕНЕНИЯ:")
        self.stdout.write("-" * 40)

        if to_add:
            self.stdout.write(
                self.style.SUCCESS(f"✅ Будет добавлено: {len(to_add)}")
            )

        if to_restock:
            self.stdout.write(
                self.style.SUCCESS(f"📦 Будет возвращено в наличие (0→1): {to_restock}")
            )

        if already_in_stock:
            self.stdout.write(f"✓ Уже в наличии (без изменений): {already_in_stock}")

        if to_remove_from_stock:
            self.stdout.write(
                self.style.WARNING(f"📤 Будет снято с наличия (→0): {to_remove_from_stock}")
            )

    def _perform_sync(self, sync_service, username, update_stock):
        """Выполнение синхронизации."""
        self.stdout.write("Выполнение синхронизации...\n")

        results = sync_service.sync_user_collection(username, update_stock)

        # Вывод результатов
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("✅ СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА"))
        self.stdout.write("=" * 60)

        self.stdout.write(f"\n📊 РЕЗУЛЬТАТЫ:")
        self.stdout.write("-" * 40)

        self.stdout.write(f"Всего в коллекции: {results['total_in_collection']}")

        if results['added'] > 0:
            self.stdout.write(
                self.style.SUCCESS(f"✅ Добавлено новых записей: {results['added']}")
            )

        if results['restocked'] > 0:
            self.stdout.write(
                self.style.SUCCESS(f"📦 Возвращено в наличие (0→1): {results['restocked']}")
            )

        if results['already_in_stock'] > 0:
            self.stdout.write(f"✓ Уже было в наличии: {results['already_in_stock']}")

        if results['out_of_stock'] > 0:
            self.stdout.write(
                self.style.WARNING(f"📤 Снято с наличия (→0): {results['out_of_stock']}")
            )

        if results['errors'] > 0:
            self.stdout.write(
                self.style.ERROR(f"❌ Ошибок: {results['errors']}")
            )

            # Показываем детали ошибок
            if results.get('error_details'):
                self.stdout.write("\n🔍 ДЕТАЛИ ОШИБОК:")
                for i, error in enumerate(results['error_details'][:5], 1):
                    self.stdout.write(
                        f"{i}. Discogs ID {error['discogs_id']}: {error['error']}"
                    )

                if len(results['error_details']) > 5:
                    self.stdout.write(f"... и еще {len(results['error_details']) - 5} ошибок")

        # Проверка результатов
        self.stdout.write("\n🔍 ПРОВЕРКА РЕЗУЛЬТАТОВ:")
        self.stdout.write("-" * 40)

        status = sync_service.verify_sync_status(username)

        if status['issues_found'] == 0:
            self.stdout.write(
                self.style.SUCCESS("✅ Все записи синхронизированы корректно!")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"⚠️  Обнаружено несоответствий: {status['issues_found']}"
                )
            )
            self.stdout.write(
                "Используйте команду 'fix_stock' для исправления"
            )
from django.core.management.base import BaseCommand
from django.utils import timezone

from records.models import Record
from records.services.collection_sync_service import CollectionSyncService


class Command(BaseCommand):
    """Команда для синхронизации коллекции пользователя из Discogs."""

    help = 'Синхронизирует коллекцию пользователя из Discogs с локальной базой данных'

    def add_arguments(self, parser):
        """Добавление аргументов команды."""
        parser.add_argument(
            'username',
            type=str,
            help='Имя пользователя Discogs'
        )

        parser.add_argument(
            '--no-update-stock',
            action='store_true',
            help='Не обнулять остатки для отсутствующих в коллекции записей'
        )

        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет сделано, но не выполнять изменения'
        )

    def handle(self, *args, **options):
        """Выполнение команды."""
        username = options['username']
        update_stock = not options['no_update_stock']
        dry_run = options['dry_run']

        self.stdout.write(f"Starting sync for user: {username}")
        self.stdout.write(f"Update stock: {update_stock}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made"))

        # Создаем сервис
        sync_service = CollectionSyncService()

        try:
            start_time = timezone.now()

            if dry_run:
                # В режиме dry-run только получаем список
                discogs_ids = sync_service._get_collection_discogs_ids(username)
                self.stdout.write(f"\nFound {len(discogs_ids)} releases in collection")

                # Показываем что будет добавлено
                existing_ids = set(
                    Record.objects.filter(
                        discogs_id__in=discogs_ids
                    ).values_list('discogs_id', flat=True)
                )

                new_ids = set(discogs_ids) - existing_ids
                self.stdout.write(f"\nWill add: {len(new_ids)} new records")

                if update_stock:
                    out_of_stock = Record.objects.filter(
                        discogs_id__isnull=False,
                        stock__gt=0
                    ).exclude(
                        discogs_id__in=discogs_ids
                    ).count()

                    self.stdout.write(f"Will set stock to 0 for: {out_of_stock} records")
            else:
                # Выполняем синхронизацию
                results = sync_service.sync_user_collection(username, update_stock)

                # Выводим результаты
                self.stdout.write("\n" + "=" * 50)
                self.stdout.write(self.style.SUCCESS("SYNC COMPLETED"))
                self.stdout.write("=" * 50)

                self.stdout.write(f"Total in collection: {results['total_in_collection']}")
                self.stdout.write(
                    self.style.SUCCESS(f"Added: {results['added']}")
                )
                self.stdout.write(
                    self.style.WARNING(f"Updated: {results['updated']}")
                )

                if results['errors'] > 0:
                    self.stdout.write(
                        self.style.ERROR(f"Errors: {results['errors']}")
                    )

                if update_stock:
                    self.stdout.write(
                        self.style.WARNING(f"Out of stock: {results['out_of_stock']}")
                    )

                # Показываем ошибки
                if results['errors'] > 0:
                    self.stdout.write("\n" + self.style.ERROR("ERRORS:"))
                    for detail in results['details']:
                        if detail['status'] == 'error':
                            self.stdout.write(
                                f"- Discogs ID {detail['discogs_id']}: {detail['error']}"
                            )

            # Время выполнения
            duration = timezone.now() - start_time
            self.stdout.write(f"\nExecution time: {duration}")

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"Error: {str(e)}")
            )
            raise
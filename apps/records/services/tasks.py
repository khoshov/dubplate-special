import logging
from typing import Dict

from celery import shared_task

from django.conf import settings
from django.utils import timezone

from apps.records.services.collection_sync_service import CollectionSyncService

logger = logging.getLogger(__name__)


@shared_task(name="sync_discogs_collection")
def sync_discogs_collection_task(
    username: str = None,
    update_stock: bool = True,
) -> Dict[str, any]:
    """Celery задача для синхронизации коллекции Discogs.

    Args:
        username: Имя пользователя Discogs (если None, берется из settings).
        update_stock: Обновлять ли остатки для записей вне коллекции.

    Returns:
        Словарь с результатами синхронизации.
    """
    # Используем username из settings если не указан
    if not username:
        username = getattr(settings, "DISCOGS_COLLECTION_USERNAME", None)
        if not username:
            logger.error("DISCOGS_COLLECTION_USERNAME not configured in settings")
            return {"error": "Username not configured"}

    logger.info(f"Starting scheduled sync for user: {username}")
    start_time = timezone.now()

    try:
        # Выполняем синхронизацию
        sync_service = CollectionSyncService()
        results = sync_service.sync_user_collection(username, update_stock)

        # Добавляем время выполнения
        results["execution_time"] = str(timezone.now() - start_time)
        results["executed_at"] = start_time.isoformat()
        results["username"] = username

        # Логируем результаты
        logger.info(
            f"Sync completed for {username}: "
            f"Added={results['added']}, "
            f"Restocked={results['restocked']}, "
            f"Out of stock={results['out_of_stock']}, "
            f"Errors={results['errors']}"
        )

        return results

    except Exception as e:
        logger.error(f"Sync failed for {username}: {str(e)}", exc_info=True)

        error_result = {
            "error": str(e),
            "username": username,
            "executed_at": start_time.isoformat(),
            "execution_time": str(timezone.now() - start_time),
        }

        return error_result


@shared_task(name="verify_sync_status")
def verify_sync_status_task(username: str = None) -> Dict[str, any]:
    """Celery задача для проверки статуса синхронизации.

    Проверяет соответствие stock коллекции без внесения изменений.

    Args:
        username: Имя пользователя Discogs.

    Returns:
        Словарь со статусом и найденными проблемами.
    """
    if not username:
        username = getattr(settings, "DISCOGS_COLLECTION_USERNAME", None)
        if not username:
            return {"error": "Username not configured"}

    try:
        sync_service = CollectionSyncService()
        status = sync_service.verify_sync_status(username)

        if status["issues_found"] > 0:
            logger.warning(f"Found {status['issues_found']} sync issues for {username}")
        else:
            logger.info(f"Sync status OK for {username}")

        return status

    except Exception as e:
        logger.error(f"Status check failed: {str(e)}", exc_info=True)
        return {"error": str(e)}

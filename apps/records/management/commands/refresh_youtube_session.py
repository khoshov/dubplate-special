from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from records.services.audio.audio_service import AudioService


class Command(BaseCommand):
    help = "Обновляет persistent browser profile для YouTube."

    def handle(self, *args, **options) -> None:
        result = AudioService.refresh_youtube_session()
        if not (result.refreshed or result.waited_for_existing_refresh):
            raise CommandError(
                result.message or "Не удалось обновить persistent profile YouTube."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Persistent profile YouTube обновлён. "
                f"profile_ready={result.profile_ready}"
            )
        )

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from records.services.audio.audio_service import AudioService


class Command(BaseCommand):
    help = (
        "Неинтерактивно инициализирует persistent browser profile для YouTube "
        "из текущего youtube-cookies.txt."
    )

    def handle(self, *args, **options) -> None:
        result = AudioService.bootstrap_youtube_session()
        if not result.profile_ready:
            raise CommandError(
                result.message or "Не удалось подготовить persistent profile YouTube."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Persistent profile YouTube подготовлен. "
                f"seeded_from_cookie_file={result.seeded_from_cookie_file}"
            )
        )

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from records.constants import YOUTUBE_SESSION_LOGIN_TIMEOUT_MS
from records.services.audio.audio_service import AudioService


class Command(BaseCommand):
    help = "Открывает headful Chromium для ручного логина в persistent YouTube profile."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--timeout-sec",
            type=int,
            default=max(1, int(YOUTUBE_SESSION_LOGIN_TIMEOUT_MS / 1000)),
            help="Максимальное время ожидания ручного логина.",
        )

    def handle(self, *args, **options) -> None:
        timeout_sec = int(options["timeout_sec"])
        self.stdout.write(
            self.style.WARNING(
                "Откройте noVNC, войдите в Google/YouTube в открывшемся Chromium "
                "и дождитесь завершения команды."
            )
        )
        result = AudioService.login_youtube_session(timeout_ms=timeout_sec * 1000)
        if not result.logged_in:
            raise CommandError(
                result.message or "Не удалось сохранить авторизованную YouTube-сессию."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Авторизованная YouTube-сессия сохранена в persistent profile."
            )
        )

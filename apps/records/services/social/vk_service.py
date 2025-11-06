import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import requests
import io, os, re, tempfile
from pathlib import Path
from django.conf import settings
logger = logging.getLogger(__name__)

ENV_PATH = Path(getattr(settings, "ENV_FILE_PATH", settings.BASE_DIR / ".env"))

_KEY_RE = {
    "VK_USER_ACCESS_TOKEN": re.compile(r"^VK_USER_ACCESS_TOKEN=.*$", re.MULTILINE),
    "VK_USER_REFRESH_TOKEN": re.compile(r"^VK_USER_REFRESH_TOKEN=.*$", re.MULTILINE),
}


def _persist_tokens_to_env(new_access: str, new_refresh: str) -> None:
    """
    Обновляет значения токенов в .env:
    - если строки есть — заменяет,
    - если нет — дописывает в конец,
    - пишет атомно (tmp+rename).
    Ничего не возвращает; ошибки логируем как warning, чтобы не ронять постинг.
    """
    try:
        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        original = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""

        updated = original
        pairs = {
            "VK_USER_ACCESS_TOKEN": f"VK_USER_ACCESS_TOKEN={new_access}",
            "VK_USER_REFRESH_TOKEN": f"VK_USER_REFRESH_TOKEN={new_refresh}",
        }

        for key, line in pairs.items():
            if _KEY_RE[key].search(updated):
                updated = _KEY_RE[key].sub(line, updated)
            else:
                # нет строки — добавим с переводом строки
                if updated and not updated.endswith("\n"):
                    updated += "\n"
                updated += line + "\n"

        # атомная запись
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            tmp.write(updated)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)

        tmp_path.replace(ENV_PATH)
        logger.info("VK: новые токены сохранены в .env (путь: %s).", ENV_PATH)
    except Exception as e:
        logger.warning(
            "VK: не удалось сохранить токены в .env (%s). Продолжаю работать с новыми токенами в памяти.", e)


# =================== Конфигурация ===================

@dataclass(frozen=True)
class VKConfig:
    """Конфигурация VK из settings.py."""
    access_token: str
    refresh_token: str
    group_id: int
    client_id: str
    client_secret: str
    device_id: str
    api_version: str

    @staticmethod
    def from_settings() -> "VKConfig":
        """Берёт параметры VK из settings.py."""
        return VKConfig(
            access_token=settings.VK_USER_ACCESS_TOKEN,
            refresh_token=settings.VK_USER_REFRESH_TOKEN,
            group_id=settings.VK_GROUP_ID,
            client_id=settings.VK_CLIENT_ID,
            client_secret=settings.VK_CLIENT_SECRET,
            device_id=settings.VK_DEVICE_ID,
            api_version=settings.VK_API_VERSION,
        )


# =================== Сервис VK ===================

class VKService:
    """Сервис публикации в сообщество ВКонтакте с автообновлением access_token."""

    API_URL = "https://api.vk.com/method"
    TOKEN_URL = "https://id.vk.ru/oauth2/auth"




    def __init__(self, config: VKConfig):
        self.access_token = config.access_token
        self.refresh_token = config.refresh_token
        self.group_id = config.group_id
        self.client_id = config.client_id
        self.client_secret = config.client_secret
        self.device_id = config.device_id
        self.api_version = config.api_version

    @classmethod
    def from_settings(cls) -> "VKService":
        """Создаёт экземпляр VKService, читая параметры из settings."""
        return cls(VKConfig.from_settings())

    @property
    def _owner_id(self) -> int:
        """Возвращает отрицательный owner_id сообщества для постинга на стену."""
        return -abs(self.group_id)







    # ----------- обновление токена -----------

    def _refresh_access_token(self) -> None:
        """
        Обновляет access_token через refresh_token.
        После успеха:
          - обновляет self.access_token / self.refresh_token,
          - сохраняет оба токена в .env (атомно).
        """
        if not (self.client_id and self.client_secret and self.refresh_token and self.device_id):
            raise RuntimeError(
                "VK: отсутствуют VK_CLIENT_ID / VK_CLIENT_SECRET / VK_USER_REFRESH_TOKEN / VK_DEVICE_ID.")

        url = "https://id.vk.ru/oauth2/auth"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "device_id": self.device_id,
        }

        logger.info("VK: обновляю access_token по refresh_token...")
        if settings.DEBUG:
            safe = {**data, "refresh_token": "***", "client_secret": "***"}
            logger.warning("VK DEBUG — refresh запрос:\nURL: %s\nПараметры: %s", url, safe)

        resp = requests.post(url, data=data, timeout=30)
        text = resp.text
        ctype = resp.headers.get("Content-Type", "")

        # В проде id.vk.ru отвечает JSON при корректном запросе.
        try:
            payload = resp.json()
        except Exception:
            short = text[:400].replace("\n", " ")
            logger.warning("VK DEBUG — ответ VK ID:\nHTTP %s\nContent-Type: %s\nТело[0..400]: %s",
                           resp.status_code, ctype, short)
            raise RuntimeError(
                f"VK: ответ VK ID не JSON (ожидался application/json). "
                f"Код={resp.status_code}. URL={url}. Тело[0..400]={short}"
            )

        if "error" in payload:
            # частый кейс: 'invalid_grant' если рефреш уже применён
            raise RuntimeError(f"VK: не удалось обновить токен ({payload})")

        new_access = payload.get("access_token", "")
        new_refresh = payload.get("refresh_token", "")  # VK отдаёт новый refresh — используем его
        if not new_access or not new_refresh:
            raise RuntimeError(f"VK: refresh вернул неожиданный ответ: {payload}")

        # Обновляем «на лету»
        self.access_token = new_access
        self.refresh_token = new_refresh

        # Пишем в .env для будущих запусков (cron и т.д.)
        _persist_tokens_to_env(new_access=new_access, new_refresh=new_refresh)

    # ----------- универсальный вызов API -----------

    def _call(self, method: str, **params: Any) -> Dict[str, Any]:
        """Вызывает метод VK API и автоматически обновляет токен при истечении."""
        data = {"access_token": self.access_token, "v": self.api_version, **params}
        url = f"{self.API_URL}/{method}"
        resp = requests.post(url, data=data, timeout=30)
        payload = resp.json()

        # Если токен протух — обновляем и пробуем заново
        if "error" in payload and payload["error"].get("error_code") == 5:
            logger.warning("VK: access_token протух, обновляю и повторяю запрос...")
            self._refresh_access_token()
            data["access_token"] = self.access_token
            resp = requests.post(url, data=data, timeout=30)
            payload = resp.json()

        if "error" in payload:
            code = payload["error"].get("error_code")
            msg = payload["error"].get("error_msg")
            raise RuntimeError(f"VK API error {code}: {msg}")

        return payload["response"]

    # ----------- публикация -----------

    def post_with_image(self, message: str, image_path: str | Path) -> int:
        """Публикует запись с фото; при ошибке — только текст."""
        path = Path(image_path)
        if not path.exists():
            logger.warning("VK: файл %s не найден, публикую только текст.", path)
            return self._post_text(message)

        try:
            upload_url = self._call("photos.getWallUploadServer", group_id=self.group_id)["upload_url"]
            with path.open("rb") as f:
                upload = requests.post(upload_url, files={"photo": (path.name, f, "image/jpeg")}, timeout=60).json()
            saved = self._call(
                "photos.saveWallPhoto",
                group_id=self.group_id,
                photo=upload["photo"],
                server=upload["server"],
                hash=upload["hash"],
            )[0]
            attachment = f"photo{saved['owner_id']}_{saved['id']}"
            data = self._call(
                "wall.post",
                owner_id=self._owner_id,
                from_group=1,
                message=message,
                attachments=attachment,
            )
            post_id = int(data["post_id"])
            logger.info("VK: запись с фото опубликована (post_id=%s)", post_id)
            return post_id
        except Exception as e:
            logger.warning("VK: ошибка при публикации с фото: %s — публикую только текст.", e)
            return self._post_text(message)

    def _post_text(self, message: str) -> int:
        """Публикует текстовую запись на стене сообщества."""
        data = self._call(
            "wall.post",
            owner_id=self._owner_id,
            from_group=1,
            message=message,
        )
        post_id = int(data["post_id"])
        logger.info("VK: текстовая запись опубликована (post_id=%s)", post_id)
        return post_id

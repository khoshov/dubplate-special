# apps/records/services/social/vk_service.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

import requests
import vk_api
from django.conf import settings
from vk_api.exceptions import ApiError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VKConfig:
    """
    Конфигурация доступа к API ВКонтакте.

    Атрибуты:
        access_token (str): Ключ доступа сообщества (не пользовательский).
        group_id (int): Идентификатор сообщества (положительный). В методах постинга
                        используется отрицательный owner_id, вычисляется автоматически.
    """

    access_token: str
    group_id: int

    @staticmethod
    def from_settings() -> "VKConfig":
        """
        Создаёт конфигурацию из Django-настроек.

        Ожидает наличия в settings:
            - VK_ACCESS_TOKEN (str)
            - VK_GROUP_ID (int, положительный без минуса)

        Returns:
            VKConfig: Сконструированная конфигурация.

        Raises:
            RuntimeError: Если параметры не заданы или заданы некорректно.
            ValueError: Если group_id не является положительным числом.
        """
        token = getattr(settings, "VK_ACCESS_TOKEN", "")
        group_id_val = getattr(settings, "VK_GROUP_ID", 0)

        if not token:
            raise RuntimeError(
                "VK: отсутствует VK_ACCESS_TOKEN. Укажите токен сообщества "
                "в .env и настройках Django."
            )

        try:
            group_id_int = int(group_id_val)
        except (TypeError, ValueError) as exc:
            raise ValueError("VK: VK_GROUP_ID должен быть целым числом.") from exc

        if group_id_int <= 0:
            raise ValueError(
                "VK: VK_GROUP_ID должен быть положительным числом (без минуса). "
                "Отрицательный owner_id будет вычислён автоматически."
            )

        return VKConfig(access_token=token, group_id=group_id_int)


class VKService:
    """
    Сервис публикации постов в сообщество ВКонтакте.

    Сервис:
      - выполняет проверку доступности API (health-check),
      - публикует текстовые посты,
      - публикует посты с изображением (обложкой записи),
      - публикует посты с аудио-превью (MP3-файлы),
      - формирует текст по полям доменной модели `Record` и `Track`.

    Поддержка токенов и методы загрузки изображений:
      1. Токен пользователя (рекомендуется):
         - Использует photos.getWallUploadServer (прямая загрузка на стену)
         - Требует права: wall, photos, groups, offline

      2. Токен сообщества (альтернатива):
         - Использует photos.getUploadServer (загрузка в альбом сообщества)
         - Фото загружается в альбом, затем прикрепляется к посту
         - Требует права: wall, photos, groups
         - Автоматически используется при ошибке 27 от первого метода

    Поддержка аудио (опционально):
      - Использует audio.getUploadServer и audio.save
      - Требует права: audio
      - ВАЖНО: VK сильно ограничил Audio API с 2018 года
      - Ошибка 270 означает, что Audio API отключен для приложения
      - При недоступности аудио API - пост публикуется без аудио

    Сервис автоматически выбирает подходящий метод загрузки в зависимости от типа токена.
    При недоступности загрузки вложений (фото/аудио) публикуется текст с доступными вложениями.

    Все сетевые шаги сопровождаются логами на русском языке.
    """

    def __init__(self, config: VKConfig):
        """
        Инициализирует сервис.

        Args:
            config (VKConfig): Конфигурация с токеном и ID сообщества.

        Raises:
            RuntimeError: Если авторизация невозможна (невалидный токен).
        """
        self._config = config
        self._vk = vk_api.VkApi(token=config.access_token)

    # --- Вспомогательные методы (внутренние) ---------------------------------

    @classmethod
    def from_settings(cls) -> "VKService":
        """
        Создаёт экземпляр сервиса, считывая настройки из Django settings.

        Returns:
            VKService: Инициализированный сервис.

        Raises:
            RuntimeError | ValueError: Если настройки заданы некорректно (см. VKConfig.from_settings()).
        """
        config = VKConfig.from_settings()
        logger.debug(
            "VKService: инициализация из настроек (group_id=%s).",
            config.group_id,
        )
        return cls(config)

    @property
    def _owner_id(self) -> int:
        """Возвращает отрицательный owner_id сообщества (требование API)."""
        return -abs(self._config.group_id)

    def _log_api_error(self, where: str, error: ApiError) -> None:
        """
        Логирует ошибку API с подсказкой по недостающим правам.

        Args:
            where (str): Этап, на котором произошла ошибка (напр.: 'photos.getWallUploadServer').
            error (ApiError): Исключение VK API.

        Примечание:
            Код 15 у ВК обычно означает «Доступ запрещён» — часто это недостаточные права.
        """
        code = getattr(error, "code", None)
        msg = str(error)
        hint = ""

        # Подсказки по правам в зависимости от места падения
        if "photos.getWallUploadServer" in where or "photos.saveWallPhoto" in where:
            if code == 15:
                hint = (
                    "Токену не хватает прав (scope 'photos'). "
                    "Получите новый токен с правами: 'photos', 'wall', 'groups'. "
                    "См. https://dev.vk.com/api/access-token/getting-started"
                )
            elif code == 27:
                hint = (
                    "Метод недоступен с токеном сообщества. "
                    "Используйте токен пользователя или система автоматически переключится на альтернативный метод."
                )
            else:
                hint = "Проверьте права токена: нужны 'photos', 'groups', 'wall'."
        elif "audio.getUploadServer" in where or "audio.save" in where:
            if code == 270:
                hint = (
                    "Аудио API недоступен (код 270: функция отключена для приложений). "
                    "VK сильно ограничил работу с аудио с 2018 года. "
                    "Рекомендуется использовать YouTube-ссылки для превью вместо загрузки MP3."
                )
            elif code == 15:
                hint = (
                    "Токену не хватает прав (scope 'audio'). "
                    "Получите новый токен с правами: 'audio', 'wall'. "
                    "Внимание: аудио API может быть недоступен в зависимости от типа приложения."
                )
            else:
                hint = (
                    "Проверьте права токена: нужны 'audio', 'wall'. "
                    "Обратите внимание, что VK сильно ограничил работу с аудио API."
                )
        elif "wall.post" in where:
            hint = (
                "Проверьте права токена сообщества: нужен доступ 'wall'. "
                "Также убедитесь, что owner_id отрицательный (мы это делаем автоматически)."
            )
        elif "groups.getById" in where:
            hint = (
                "Проверьте, что передан правильный VK_GROUP_ID (положительный без минуса) "
                "и токен действительно принадлежит этому сообществу."
            )

        logger.error(
            "VK API: ошибка на этапе «%s»: код=%s, сообщение=%s. %s",
            where,
            code,
            msg,
            hint,
        )

    def _get_wall_upload_url(self) -> str:
        """
        Получает адрес загрузки фото на стену сообщества.

        Returns:
            str: URL для загрузки файла.

        Raises:
            ApiError: Ошибка ВК при запросе upload_url (недостаток прав и т.п.).
        """
        logger.debug(
            "VK: запрашиваю адрес загрузки фото (photos.getWallUploadServer, group_id=%s).",
            abs(self._config.group_id),
        )
        try:
            data: Dict[str, Any] = self._vk.method(
                "photos.getWallUploadServer", {"group_id": abs(self._config.group_id)}
            )
            upload_url = data["upload_url"]
            logger.debug("VK: адрес загрузки фото получен.")
            return upload_url
        except ApiError as err:
            self._log_api_error("photos.getWallUploadServer", err)
            raise

    def _get_album_upload_url(self) -> str:
        """
        Получает адрес загрузки фото в альбом сообщества (альтернативный метод).

        Этот метод может работать с токеном сообщества, в отличие от getWallUploadServer.
        Фото загружается в основной альбом сообщества, затем прикрепляется к посту.

        Returns:
            str: URL для загрузки файла.

        Raises:
            ApiError: Ошибка ВК при запросе upload_url.
        """
        logger.debug(
            "VK: запрашиваю адрес загрузки фото в альбом (photos.getUploadServer, group_id=%s).",
            abs(self._config.group_id),
        )
        try:
            data: Dict[str, Any] = self._vk.method(
                "photos.getUploadServer", {"group_id": abs(self._config.group_id)}
            )
            upload_url = data["upload_url"]
            logger.debug("VK: адрес загрузки фото в альбом получен.")
            return upload_url
        except ApiError as err:
            self._log_api_error("photos.getUploadServer", err)
            raise

    def _save_wall_photo(self, upload_resp: Dict[str, Any]) -> Dict[str, Any]:
        """
        Сохраняет загруженную фотографию в альбом стены сообщества.

        Args:
            upload_resp (Dict[str, Any]): Ответ от загрузчика (photo/server/hash).

        Returns:
            Dict[str, Any]: Описание сохранённой фотографии (owner_id, id, ...).

        Raises:
            ApiError: Ошибка ВК при сохранении фото (недостаток прав и т.п.).
        """
        logger.debug("VK: сохраняю фото на стене (photos.saveWallPhoto).")
        try:
            saved_list = self._vk.method(
                "photos.saveWallPhoto",
                {
                    "group_id": abs(self._config.group_id),
                    "photo": upload_resp["photo"],
                    "server": upload_resp["server"],
                    "hash": upload_resp["hash"],
                },
            )
            saved = saved_list[0]
            logger.debug(
                "VK: фото сохранено (owner_id=%s, id=%s).",
                saved.get("owner_id"),
                saved.get("id"),
            )
            return saved
        except ApiError as err:
            self._log_api_error("photos.saveWallPhoto", err)
            raise

    def _save_album_photo(self, upload_resp: Dict[str, Any]) -> Dict[str, Any]:
        """
        Сохраняет загруженную фотографию в альбом сообщества (альтернативный метод).

        Args:
            upload_resp (Dict[str, Any]): Ответ от загрузчика (photos_list/server/hash).

        Returns:
            Dict[str, Any]: Описание сохранённой фотографии (owner_id, id, ...).

        Raises:
            ApiError: Ошибка ВК при сохранении фото.
        """
        logger.debug("VK: сохраняю фото в альбоме (photos.save).")
        try:
            saved_list = self._vk.method(
                "photos.save",
                {
                    "group_id": abs(self._config.group_id),
                    "photos_list": upload_resp.get("photos_list")
                    or upload_resp.get("photo"),
                    "server": upload_resp["server"],
                    "hash": upload_resp["hash"],
                },
            )
            saved = saved_list[0]
            logger.debug(
                "VK: фото сохранено в альбоме (owner_id=%s, id=%s).",
                saved.get("owner_id"),
                saved.get("id"),
            )
            return saved
        except ApiError as err:
            self._log_api_error("photos.save", err)
            raise

    def _get_audio_upload_url(self) -> str:
        """
        Получает адрес загрузки аудио-файла.

        Returns:
            str: URL для загрузки аудио-файла.

        Raises:
            ApiError: Ошибка ВК при запросе upload_url.
        """
        logger.debug("VK: запрашиваю адрес загрузки аудио (audio.getUploadServer).")
        try:
            data: Dict[str, Any] = self._vk.method("audio.getUploadServer", {})
            upload_url = data["upload_url"]
            logger.debug("VK: адрес загрузки аудио получен.")
            return upload_url
        except ApiError as err:
            self._log_api_error("audio.getUploadServer", err)
            raise

    def _save_audio(
        self,
        upload_resp: Dict[str, Any],
        artist: str,
        title: str,
    ) -> Dict[str, Any]:
        """
        Сохраняет загруженный аудио-файл в аудиозаписи пользователя.

        ВАЖНО: VK API не позволяет напрямую загружать аудио в сообщества через токен.
        Аудио сохраняется в личные записи владельца токена, затем прикрепляется к посту.

        Args:
            upload_resp (Dict[str, Any]): Ответ от загрузчика (audio/server/hash).
            artist (str): Имя артиста.
            title (str): Название трека.

        Returns:
            Dict[str, Any]: Описание сохранённого аудио (owner_id, id, ...).

        Raises:
            ApiError: Ошибка ВК при сохранении аудио.
        """
        logger.debug("VK: сохраняю аудио (audio.save).")
        try:
            # VK API сохраняет аудио в личные записи владельца токена
            # Параметр group_id недоступен для audio.save
            saved = self._vk.method(
                "audio.save",
                {
                    "audio": upload_resp["audio"],
                    "server": upload_resp["server"],
                    "hash": upload_resp["hash"],
                    "artist": artist,
                    "title": title,
                },
            )
            logger.debug(
                "VK: аудио сохранено (owner_id=%s, id=%s).",
                saved.get("owner_id"),
                saved.get("id"),
            )
            logger.info(
                "VK: аудио будет прикреплено к посту от имени пользователя (owner_id=%s)",
                saved.get("owner_id"),
            )
            return saved
        except ApiError as err:
            self._log_api_error("audio.save", err)
            raise

    def _upload_audio(
        self, audio_path: str | Path, artist: str, title: str
    ) -> Optional[str]:
        """
        Загружает аудио-файл на VK и возвращает строку вложения.

        Args:
            audio_path (str | Path): Путь к локальному аудио-файлу.
            artist (str): Имя артиста.
            title (str): Название трека.

        Returns:
            Optional[str]: Строка вложения 'audio<owner_id>_<id>' или None при ошибке.
        """
        path = Path(audio_path)
        if not path.exists():
            logger.warning(
                "VK: аудио-файл не найден (%s). Пропускаю загрузку аудио.", path
            )
            return None

        logger.info("VK: загружаю аудио-файл: %s", path)

        # 1) Получаем адрес загрузки
        try:
            upload_url = self._get_audio_upload_url()
        except ApiError as err:
            err_code = getattr(err, "code", None)
            if err_code == 270:
                logger.warning(
                    "VK: аудио API недоступен (код 270: функция отключена). "
                    "Аудио не будет загружено. Используйте YouTube для превью."
                )
            else:
                logger.warning(
                    "VK: ошибка при получении upload_url для аудио. "
                    "Аудио не будет загружено."
                )
            return None

        # 2) Загружаем файл
        logger.debug("VK: загружаю аудио-файл на сервер ВК (POST upload_url).")
        try:
            with path.open("rb") as f:
                resp = requests.post(
                    upload_url,
                    files={"file": (path.name, f, "audio/mpeg")},
                    timeout=60,
                )
                resp.raise_for_status()
            upload_resp = resp.json()
        except requests.HTTPError as http_err:
            logger.exception(
                "VK: HTTP-ошибка при загрузке аудио на сервер ВК: %s", http_err
            )
            return None
        except requests.RequestException as net_err:
            logger.exception(
                "VK: сеть/соединение: ошибка при загрузке аудио: %s", net_err
            )
            return None

        # 3) Сохраняем аудио
        try:
            saved = self._save_audio(upload_resp, artist, title)
            attachment = f"audio{saved['owner_id']}_{saved['id']}"
            logger.debug("VK: аудио-вложение подготовлено: %s", attachment)
            return attachment
        except ApiError:
            logger.warning(
                "VK: ошибка при сохранении аудио (audio.save). "
                "Аудио не будет прикреплено к посту."
            )
            return None

    def _wall_post(self, message: str, attachment: Optional[str] = None) -> int:
        """
        Публикует запись на стене сообщества.

        Args:
            message (str): Текст публикации.
            attachment (Optional[str]): Строка вложений ВК (напр., 'photo<owner_id>_<id>').

        Returns:
            int: Идентификатор поста (post_id).

        Raises:
            ApiError: Ошибка ВК при публикации.
        """
        logger.debug(
            "VK: вызываю wall.post (owner_id=%s, есть_вложение=%s).",
            self._owner_id,
            bool(attachment),
        )
        try:
            resp: Dict[str, Any] = self._vk.method(
                "wall.post",
                {
                    "owner_id": self._owner_id,
                    "message": message,
                    "attachment": attachment,
                    "from_group": 1,
                },
            )
            post_id = int(resp.get("post_id"))
            logger.info("VK: запись опубликована, post_id=%s.", post_id)
            return post_id
        except ApiError as err:
            self._log_api_error("wall.post", err)
            raise

    # --- Публичные методы -----------------------------------------------------

    def health_check(self) -> bool:
        """
        Выполняет базовую проверку доступности API и прав доступа.

        Метод:
          1) Проверяет доступ к сообществу (groups.getById).
          2) Пробует получить адрес загрузки изображения (photos.getWallUploadServer).
          3) Если метод 2 недоступен (код 27), пробует альтернативный через альбом.

        Returns:
            bool: True, если проверки прошли успешно; False, если были критические ошибки.

        Примечание:
            Этот метод не является обязательным в рантайме, но удобен при отладке конфигурации.
        """
        ok = True

        logger.info("VK: проверка конфигурации и прав доступа (health-check).")
        try:
            self._vk.method("groups.getById", {"group_id": abs(self._config.group_id)})
            logger.debug("VK: groups.getById — ок.")
        except ApiError as err:
            ok = False
            self._log_api_error("groups.getById", err)

        # Пробуем прямую загрузку на стену
        try:
            self._get_wall_upload_url()
            logger.debug(
                "VK: photos.getWallUploadServer — ок (токен пользователя с полными правами)."
            )
        except ApiError as err:
            err_code = getattr(err, "code", None)
            # Если код 15 или 27 — пробуем альтернативный метод
            if err_code in (15, 27):
                if err_code == 15:
                    logger.info(
                        "VK: photos.getWallUploadServer недоступен (код 15: недостаточно прав/scope)."
                    )
                else:
                    logger.info(
                        "VK: photos.getWallUploadServer недоступен (код 27: нужен токен пользователя)."
                    )
                logger.info("VK: проверяю альтернативный метод через альбом...")
                try:
                    self._get_album_upload_url()
                    logger.debug(
                        "VK: photos.getUploadServer — ок (альтернативный метод работает)."
                    )
                except ApiError:
                    ok = False
                    logger.error("VK: оба метода загрузки фото недоступны.")
            else:
                ok = False

        if ok:
            logger.info("VK: health-check пройден успешно.")
        else:
            logger.warning(
                "VK: health-check завершён с ошибками. Проверьте логи выше и права токена сообщества."
            )
        return ok

    def post_text(self, message: str) -> int:
        """
        Публикует текстовую запись в сообщества.

        Args:
            message (str): Текст публикации.

        Returns:
            int: Идентификатор поста.

        Raises:
            ApiError: Ошибка ВК при публикации.
        """
        logger.info("VK: публикую текстовую запись (без изображения).")
        return self._wall_post(message=message, attachment=None)

    def post_with_image(self, message: str, image_path: str | Path) -> int:
        """
        Публикует запись с изображением (локальный файл) в сообщество.

        Использует стратегию с несколькими попытками:
        1. Пытается загрузить напрямую на стену (photos.getWallUploadServer)
        2. При ошибке 27 (требуется токен пользователя) пробует альтернативный метод
           через загрузку в альбом сообщества (photos.getUploadServer)
        3. Если все методы не сработали — публикует только текст

        Args:
            message (str): Текст публикации.
            image_path (str | Path): Путь к локальному файлу изображения.

        Returns:
            int: Идентификатор поста.

        Падения и обработка:
            - Если файл не найден — публикуется только текст (warning в лог).
            - Если ВК не разрешает работу с фото (недостаточно прав) —
              фиксируется ошибка и выполняется публикация только текста.
        """
        path = Path(image_path)
        if not path.exists():
            logger.warning(
                "VK: изображение не найдено (%s). Публикую только текст.", path
            )
            return self.post_text(message)

        logger.info("VK: публикую запись с изображением: %s", path)

        # Стратегия 1: Прямая загрузка на стену (требует токен пользователя)
        upload_url = None
        use_album_method = False

        try:
            upload_url = self._get_wall_upload_url()
            logger.debug("VK: использую метод прямой загрузки на стену.")
        except ApiError as err:
            err_code = getattr(err, "code", None)
            # Код 27 = метод недоступен с токеном сообщества
            # Код 15 = недостаточно прав (нет нужных scope)
            if err_code in (15, 27):
                if err_code == 15:
                    logger.info(
                        "VK: недостаточно прав для photos.getWallUploadServer (код 15). "
                        "Токену не хватает scope 'photos'. Пробую альтернативный метод через альбом."
                    )
                else:
                    logger.info(
                        "VK: прямая загрузка на стену недоступна (требуется токен пользователя). "
                        "Пробую альтернативный метод через альбом сообщества."
                    )
                use_album_method = True
            else:
                logger.warning(
                    "VK: нет прав на загрузку фото или ошибка API. Публикую только текст."
                )
                return self.post_text(message)

        # Стратегия 2: Загрузка в альбом сообщества (работает с токеном сообщества)
        if use_album_method:
            try:
                upload_url = self._get_album_upload_url()
                logger.debug("VK: использую метод загрузки в альбом сообщества.")
            except ApiError:
                logger.warning(
                    "VK: альтернативный метод загрузки также недоступен. Публикую только текст."
                )
                return self.post_text(message)

        # Загружаем файл на полученный upload_url
        logger.debug("VK: загружаю файл на сервер ВК (POST upload_url).")
        try:
            with path.open("rb") as f:
                resp = requests.post(
                    upload_url,
                    files={"photo": (path.name, f, "image/jpeg")},
                    timeout=30,
                )
                resp.raise_for_status()
            upload_resp = resp.json()
        except requests.HTTPError as http_err:
            logger.exception(
                "VK: HTTP-ошибка при загрузке изображения на сервер ВК: %s", http_err
            )
            return self.post_text(message)
        except requests.RequestException as net_err:
            logger.exception(
                "VK: сеть/соединение: ошибка при загрузке изображения: %s", net_err
            )
            return self.post_text(message)

        # Сохраняем фото подходящим методом и постим с вложением
        try:
            if use_album_method:
                saved = self._save_album_photo(upload_resp)
            else:
                saved = self._save_wall_photo(upload_resp)

            attachment = f"photo{saved['owner_id']}_{saved['id']}"
            logger.debug("VK: вложение подготовлено: %s", attachment)
            return self._wall_post(message=message, attachment=attachment)
        except ApiError:
            logger.warning(
                "VK: недостаточно прав для сохранения фото или публикации с фото. "
                "Публикую только текст."
            )
            return self.post_text(message)

    def diagnose(self) -> None:
        """
        Выполняет расширенную диагностику прав и настроек сообщества.

        Метод логирует:
          - groups.getSettings (состояние разделов: 'photos', 'wall' и т.д.);
          - попытку получить upload_url (photos.getWallUploadServer);
          - попытку получить альтернативный upload_url (photos.getUploadServer).

        Ничего не возвращает; вся информация уходит в лог.
        """
        logger.info("VK: диагностика прав и настроек сообщества (diagnose).")

        # 1) Настройки сообщества
        try:
            # groups.getSettings требует ключ сообщества с правом управления
            settings_info = self._vk.method(
                "groups.getSettings", {"group_id": abs(self._config.group_id)}
            )
            enabled_sections = settings_info.get("sections", {})
            # В старых версиях API структура может отличаться — подстрахуемся
            photos_enabled = bool(enabled_sections.get("photos", 0)) or (
                settings_info.get("photos") == 1
            )
            wall_setting = settings_info.get(
                "wall"
            )  # 0/1/2/3 (см. доку), 1/2 — включено
            wall_enabled = wall_setting in (1, 2)

            logger.info(
                "VK: groups.getSettings — ОК. Разделы: photos=%s, wall=%s (raw wall=%s).",
                photos_enabled,
                wall_enabled,
                wall_setting,
            )
            if not photos_enabled:
                logger.warning(
                    "VK: в сообществе выключен раздел «Фотографии». Включите его в «Управление → Разделы», "
                    "иначе загрузка изображений на стену будет невозможна."
                )
            if not wall_enabled:
                logger.warning(
                    "VK: в сообществе выключена «Стена» для записей. Включите её, иначе публикации невозможны."
                )
        except ApiError as err:
            self._log_api_error("groups.getSettings", err)

        # 2) Проба прямой загрузки на стену
        try:
            url = self._get_wall_upload_url()
            logger.info("VK: photos.getWallUploadServer — адрес получен: %s", url)
            logger.info("VK: используется токен пользователя с полными правами.")
        except ApiError as err:
            err_code = getattr(err, "code", None)
            if err_code in (15, 27):
                if err_code == 15:
                    logger.info(
                        "VK: photos.getWallUploadServer недоступен (код 15: недостаточно прав). "
                        "Токену не хватает scope 'photos'. Для получения токена с нужными правами: "
                        "https://dev.vk.com/api/access-token/getting-started"
                    )
                else:
                    logger.info(
                        "VK: photos.getWallUploadServer недоступен (код 27: требуется токен пользователя)."
                    )
                logger.info("VK: проверяю альтернативный метод через альбом...")

                # 3) Проба альтернативного метода через альбом
                try:
                    album_url = self._get_album_upload_url()
                    logger.info(
                        "VK: photos.getUploadServer (альбом) — адрес получен: %s",
                        album_url,
                    )
                    logger.info(
                        "VK: альтернативный метод доступен. "
                        "Фото будут загружаться в альбом сообщества, затем прикрепляться к постам."
                    )
                except ApiError:
                    logger.error(
                        "VK: оба метода загрузки фото недоступны. "
                        "Рекомендуется получить новый токен с правами 'photos', 'wall', 'groups'."
                    )
            else:
                logger.warning(
                    "VK: photos.getWallUploadServer вернул неожиданную ошибку. "
                    "Если раздел «Фотографии» выключен — включите и повторите диагностику."
                )

    def post_record(
        self, record: Any, *, message_template: Optional[str] = None
    ) -> int:
        """
        Публикует запись доменной модели `Record` на стену сообщества.

        Формирует краткий текст из полей записи и публикует:
          - с обложкой (если есть локальный файл),
          - иначе — только текст.

        Args:
            record (Any): Объект доменной модели `Record` (ожидаются атрибуты .title,
                          .artists (M2M c .name), .label?.name, .catalog_number,
                          .price?, .stock?, .cover_image?.path).
            message_template (Optional[str]): Необязательный шаблон текста.
                Если задан, форматирует .format(title, artists, label, catalog_number, price, stock).

        Returns:
            int: Идентификатор поста.
        """
        # Сбор короткого текста
        title: str = getattr(record, "title", "Без названия")
        artists_qs = getattr(record, "artists", None)
        if artists_qs is not None and hasattr(artists_qs, "all"):
            artists_str = (
                ", ".join(a.name for a in artists_qs.all()[:3])
                or "Неизвестный исполнитель"
            )
        else:
            artists_str = "Неизвестный исполнитель"

        label_obj = getattr(record, "label", None)
        label_name: str = getattr(label_obj, "name", "-") if label_obj else "-"

        catalog_number: str = getattr(record, "catalog_number", "-") or "-"
        price = getattr(record, "price", None)
        stock = getattr(record, "stock", None)

        if message_template:
            message = message_template.format(
                title=title,
                artists=artists_str,
                label=label_name,
                catalog_number=catalog_number,
                price=price,
                stock=stock,
            )
        else:
            parts = [f"{artists_str} — {title}"]
            meta = []
            if label_name != "-":
                meta.append(label_name)
            if catalog_number != "-":
                meta.append(catalog_number)
            if meta:
                parts.append(" / ".join(meta))
            if price is not None:
                parts.append(f"Цена: {price}")
            if stock is not None:
                parts.append(f"Склад: {stock}")
            message = "\n".join(parts)

        # Обложка — публикуем с фото, если есть локальный файл
        cover = getattr(record, "cover_image", None)
        cover_path: Optional[str] = getattr(cover, "path", None) if cover else None

        logger.debug(
            "VK: подготовлен текст (%d символов). Есть_обложка=%s, record_id=%s.",
            len(message),
            bool(cover_path),
            getattr(record, "pk", None),
        )

        if cover_path:
            return self.post_with_image(message, cover_path)

        return self.post_text(message)

    def post_record_with_audio(
        self, record: Any, *, message_template: Optional[str] = None
    ) -> int:
        """
        Публикует запись доменной модели `Record` со ВСЕМИ аудио-треками на стену сообщества.

        Формирует краткий текст из полей записи и публикует:
          - с обложкой (если есть локальный файл),
          - со ВСЕМИ аудио-превью из треков (если есть MP3-файлы),
          - иначе — только текст.

        Args:
            record (Any): Объект доменной модели `Record` (ожидаются атрибуты .title,
                          .artists (M2M c .name), .label?.name, .catalog_number,
                          .price?, .stock?, .cover_image?.path, .tracks (related manager)).
            message_template (Optional[str]): Необязательный шаблон текста.
                Если задан, форматирует .format(title, artists, label, catalog_number, price, stock).

        Returns:
            int: Идентификатор поста.
        """
        # Сбор короткого текста
        title: str = getattr(record, "title", "Без названия")
        artists_qs = getattr(record, "artists", None)
        if artists_qs is not None and hasattr(artists_qs, "all"):
            artists_str = (
                ", ".join(a.name for a in artists_qs.all()[:3])
                or "Неизвестный исполнитель"
            )
        else:
            artists_str = "Неизвестный исполнитель"

        label_obj = getattr(record, "label", None)
        label_name: str = getattr(label_obj, "name", "-") if label_obj else "-"

        catalog_number: str = getattr(record, "catalog_number", "-") or "-"
        price = getattr(record, "price", None)
        stock = getattr(record, "stock", None)

        # Формируем текст
        if message_template:
            message = message_template.format(
                title=title,
                artists=artists_str,
                label=label_name,
                catalog_number=catalog_number,
                price=price,
                stock=stock,
            )
        else:
            parts = [f"{artists_str} — {title}"]
            meta = []
            if label_name != "-":
                meta.append(label_name)
            if catalog_number != "-":
                meta.append(catalog_number)
            if meta:
                parts.append(" / ".join(meta))
            if price is not None:
                parts.append(f"Цена: {price} ₽")
            if stock is not None:
                parts.append(f"В наличии: {stock} шт.")
            message = "\n".join(parts)

        logger.debug(
            "VK: подготовлен текст для релиза (%d символов). record_id=%s.",
            len(message),
            getattr(record, "pk", None),
        )

        # Собираем вложения
        attachments = []

        # 1. Обложка релиза
        cover = getattr(record, "cover_image", None)
        cover_path: Optional[str] = getattr(cover, "path", None) if cover else None

        if cover_path:
            path = Path(cover_path)
            if not path.exists():
                logger.warning("VK: обложка не найдена (%s).", path)
            else:
                logger.info("VK: загружаю обложку релиза: %s", path)
                upload_url = None
                use_album_method = False

                try:
                    upload_url = self._get_wall_upload_url()
                except ApiError as err:
                    err_code = getattr(err, "code", None)
                    if err_code in (15, 27):
                        use_album_method = True

                if use_album_method:
                    try:
                        upload_url = self._get_album_upload_url()
                    except ApiError:
                        upload_url = None

                if upload_url:
                    try:
                        with path.open("rb") as f:
                            resp = requests.post(
                                upload_url,
                                files={"photo": (path.name, f, "image/jpeg")},
                                timeout=30,
                            )
                            resp.raise_for_status()
                        upload_resp = resp.json()

                        if use_album_method:
                            saved = self._save_album_photo(upload_resp)
                        else:
                            saved = self._save_wall_photo(upload_resp)

                        photo_attachment = f"photo{saved['owner_id']}_{saved['id']}"
                        attachments.append(photo_attachment)
                        logger.info("VK: обложка загружена: %s", photo_attachment)
                    except Exception as e:
                        logger.warning("VK: ошибка при загрузке обложки: %s", e)

        # 2. Все аудио-треки релиза
        tracks = getattr(record, "tracks", None)
        if tracks is not None and hasattr(tracks, "all"):
            # Получаем треки с аудио, отсортированные по position_index
            audio_tracks = tracks.exclude(audio_preview="").order_by("position_index")
            audio_count = audio_tracks.count()

            logger.info("VK: найдено треков с аудио: %d", audio_count)

            if audio_count > 0:
                # VK позволяет до 10 вложений, минус 1 для обложки = до 9 аудио
                max_audio = 9 if cover_path else 10

                for idx, track in enumerate(audio_tracks[:max_audio], start=1):
                    audio_preview = getattr(track, "audio_preview", None)
                    audio_path = (
                        getattr(audio_preview, "path", None) if audio_preview else None
                    )

                    if audio_path:
                        track_title = getattr(track, "title", "Без названия")
                        logger.info(
                            "VK: загружаю аудио %d/%d: %s - %s",
                            idx,
                            min(audio_count, max_audio),
                            track_title,
                            audio_path,
                        )

                        audio_attachment = self._upload_audio(
                            audio_path, artists_str, track_title
                        )
                        if audio_attachment:
                            attachments.append(audio_attachment)
                            logger.info(
                                "VK: аудио %d загружено: %s", idx, audio_attachment
                            )
                        else:
                            logger.warning("VK: аудио %d НЕ загружено", idx)

                if audio_count > max_audio:
                    logger.warning(
                        "VK: релиз содержит %d аудио, но можно загрузить только %d (лимит VK)",
                        audio_count,
                        max_audio,
                    )
        else:
            logger.info("VK: у релиза нет треков с аудио")

        # 3. Публикуем пост
        attachment_str = ",".join(attachments) if attachments else None

        logger.info(
            "VK: публикую релиз с %d вложениями: %s",
            len(attachments),
            attachment_str or "нет вложений",
        )

        return self._wall_post(message=message, attachment=attachment_str)

    def post_track(self, track: Any, *, message_template: Optional[str] = None) -> int:
        """
        Публикует трек доменной модели `Track` на стену сообщества.

        Формирует текст из полей трека и связанной записи, публикует:
          - с обложкой записи (если есть локальный файл),
          - с аудио-превью (если есть локальный MP3),
          - добавляет ссылку на YouTube превью (если есть),
          - иначе — только текст.

        Args:
            track (Any): Объект доменной модели `Track` (ожидаются атрибуты .title,
                        .position, .duration, .youtube_url, .audio_preview,
                        .record с полями .title, .artists, .label, .catalog_number, .cover_image).
            message_template (Optional[str]): Необязательный шаблон текста.
                Если задан, форматирует .format(track_title, track_position, track_duration,
                                                  record_title, artists, label, catalog_number).

        Returns:
            int: Идентификатор поста.
        """
        # Информация о треке
        track_title: str = getattr(track, "title", "Без названия")
        track_position: str = getattr(track, "position", "")
        track_duration: str = getattr(track, "duration", "")
        youtube_url: Optional[str] = getattr(track, "youtube_url", None)
        audio_preview = getattr(track, "audio_preview", None)

        # Информация о записи
        record = getattr(track, "record", None)
        if record:
            record_title: str = getattr(record, "title", "")
            artists_qs = getattr(record, "artists", None)
            if artists_qs is not None and hasattr(artists_qs, "all"):
                artists_str = (
                    ", ".join(a.name for a in artists_qs.all()[:3])
                    or "Неизвестный исполнитель"
                )
            else:
                artists_str = "Неизвестный исполнитель"

            label_obj = getattr(record, "label", None)
            label_name: str = getattr(label_obj, "name", "-") if label_obj else "-"
            catalog_number: str = getattr(record, "catalog_number", "-") or "-"
            cover = getattr(record, "cover_image", None)
            cover_path: Optional[str] = getattr(cover, "path", None) if cover else None
        else:
            record_title = ""
            artists_str = "Неизвестный исполнитель"
            label_name = "-"
            catalog_number = "-"
            cover_path = None

        # Путь к аудио-превью
        audio_path: Optional[str] = (
            getattr(audio_preview, "path", None) if audio_preview else None
        )

        logger.debug("=" * 80)
        logger.debug(audio_path)
        logger.debug("=" * 80)

        # Логируем информацию об аудио
        logger.debug(
            "VK: audio_preview=%s, audio_path=%s",
            bool(audio_preview),
            audio_path if audio_path else "None",
        )

        # Формируем текст поста
        if message_template:
            message = message_template.format(
                track_title=track_title,
                track_position=track_position,
                track_duration=track_duration,
                record_title=record_title,
                artists=artists_str,
                label=label_name,
                catalog_number=catalog_number,
            )
        else:
            # Стандартный формат
            parts = []

            # Заголовок: Артист - Название трека
            header = f"{artists_str} — {track_title}"
            if track_position:
                header = f"[{track_position}] {header}"
            if track_duration:
                header = f"{header} ({track_duration})"
            parts.append(header)

            # Информация о релизе
            if record_title:
                parts.append(f"💿 {record_title}")

            # Метаданные
            meta = []
            if label_name != "-":
                meta.append(f"🏷 {label_name}")
            if catalog_number != "-":
                meta.append(f"📋 {catalog_number}")
            if meta:
                parts.append(" • ".join(meta))

            # YouTube превью
            if youtube_url:
                parts.append(f"\n🎧 Превью: {youtube_url}")

            message = "\n".join(parts)

        logger.debug(
            "VK: подготовлен текст для трека (%d символов). "
            "Есть_обложка=%s, есть_аудио=%s, есть_youtube=%s, track_id=%s.",
            len(message),
            bool(cover_path),
            bool(audio_path),
            bool(youtube_url),
            getattr(track, "pk", None),
        )

        # Собираем вложения
        attachments = []

        # 1. Обложка записи
        if cover_path:
            path = Path(cover_path)
            if not path.exists():
                logger.warning(
                    "VK: изображение не найдено (%s). Публикую без обложки.", path
                )
            else:
                logger.info("VK: загружаю обложку: %s", path)
                # Используем существующую логику загрузки фото
                upload_url = None
                use_album_method = False

                try:
                    upload_url = self._get_wall_upload_url()
                    logger.debug("VK: использую метод прямой загрузки на стену.")
                except ApiError as err:
                    err_code = getattr(err, "code", None)
                    if err_code in (15, 27):
                        use_album_method = True
                    else:
                        logger.warning("VK: не удалось получить upload_url для фото.")

                if use_album_method:
                    try:
                        upload_url = self._get_album_upload_url()
                        logger.debug(
                            "VK: использую метод загрузки в альбом сообщества."
                        )
                    except ApiError:
                        logger.warning(
                            "VK: альтернативный метод загрузки также недоступен."
                        )
                        upload_url = None

                if upload_url:
                    try:
                        with path.open("rb") as f:
                            resp = requests.post(
                                upload_url,
                                files={"photo": (path.name, f, "image/jpeg")},
                                timeout=30,
                            )
                            resp.raise_for_status()
                        upload_resp = resp.json()

                        if use_album_method:
                            saved = self._save_album_photo(upload_resp)
                        else:
                            saved = self._save_wall_photo(upload_resp)

                        photo_attachment = f"photo{saved['owner_id']}_{saved['id']}"
                        attachments.append(photo_attachment)
                        logger.debug(
                            "VK: фото-вложение добавлено: %s", photo_attachment
                        )
                    except Exception as e:
                        logger.warning("VK: ошибка при загрузке обложки: %s", e)

        # 2. Аудио-превью
        logger.debug("=" * 80)
        logger.debug(audio_path)
        logger.debug("=" * 80)

        if audio_path:
            logger.info("VK: обнаружено аудио-превью, загружаю: %s", audio_path)
            audio_attachment = self._upload_audio(audio_path, artists_str, track_title)
            if audio_attachment:
                attachments.append(audio_attachment)
                logger.info(
                    "VK: аудио-вложение успешно добавлено: %s", audio_attachment
                )
            else:
                logger.warning(
                    "VK: аудио-вложение НЕ добавлено. "
                    "Возможно, Audio API недоступен (ошибка 270) или недостаточно прав."
                )
        else:
            logger.debug("VK: audio_path пустой, аудио не будет загружено")

        # Публикуем пост
        attachment_str = ",".join(attachments) if attachments else None

        logger.info(
            "VK: публикую трек с %d вложениями: %s",
            len(attachments),
            attachment_str or "нет вложений",
        )

        return self._wall_post(message=message, attachment=attachment_str)

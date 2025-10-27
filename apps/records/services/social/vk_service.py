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
      - формирует текст по полям доменной модели `Record`.

    Требования к правам токена (ключа сообщества):
      - права на работу со стеной:     wall
      - права на фотографии:           photos
      - права на доступ к сообществам: groups
      - полезно «длительное действие»: offline (чтобы не истекал быстро)

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
            hint = (
                "Проверьте права токена сообщества: нужны 'photos', 'groups', 'wall'. "
                "Токен должен быть токеном сообщества (не пользовательским)."
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

        logger.error("VK API: ошибка на этапе «%s»: код=%s, сообщение=%s. %s", where, code, msg, hint)

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

        Returns:
            bool: True, если оба шага прошли успешно; False, если были ошибки (логи подробно подскажут где).

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

        try:
            # Если прав недостаточно — здесь упадём и подскажем, какие права нужны.
            self._get_wall_upload_url()
            logger.debug("VK: photos.getWallUploadServer — ок.")
        except ApiError:
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

        # 1) Получаем адрес загрузки
        try:
            upload_url = self._get_wall_upload_url()
        except ApiError:
            logger.warning(
                "VK: нет прав на загрузку фото или ошибка API. Публикую только текст."
            )
            return self.post_text(message)

        # 2) Загружаем файл
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

        # 3) Сохраняем фото и постим с вложением
        try:
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
          - попытку получить upload_url (photos.getWallUploadServer).

        Ничего не возвращает; вся информация уходит в лог.
        """
        logger.info("VK: диагностика прав и настроек сообщества (diagnose).")

        # 1) Настройки сообщества
        try:
            # groups.getSettings требует ключ сообщества с правом управления
            settings_info = self._vk.method("groups.getSettings", {"group_id": abs(self._config.group_id)})
            enabled_sections = settings_info.get("sections", {})
            # В старых версиях API структура может отличаться — подстрахуемся
            photos_enabled = bool(enabled_sections.get("photos", 0)) or (settings_info.get("photos") == 1)
            wall_setting = settings_info.get("wall")  # 0/1/2/3 (см. доку), 1/2 — включено
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

        # 2) Проба upload_url
        try:
            url = self._get_wall_upload_url()
            logger.info("VK: photos.getWallUploadServer — адрес получен: %s", url)
        except ApiError:
            logger.warning(
                "VK: photos.getWallUploadServer по-прежнему не даёт upload_url. "
                "Если раздел «Фотографии» выключен — включите и повторите диагностику."
            )


    def post_record(self, record: Any, *, message_template: Optional[str] = None) -> int:
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
            artists_str = ", ".join(a.name for a in artists_qs.all()[:3]) or "Неизвестный исполнитель"
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

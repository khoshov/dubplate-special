from __future__ import annotations

import logging
import re
import time
from calendar import month_abbr
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence

import requests
import vk_api
from django.conf import settings
from vk_api.exceptions import ApiError

from records.models import Record

logger = logging.getLogger(__name__)

# Фиксированная задержка для отложенной публикации (в секундах)
POSTPONE_DELAY_SECONDS = 15 * 60


# =============================================================================
# Config
# =============================================================================


@dataclass(frozen=True)
class VKConfig:
    """
    Конфигурация доступа к API ВКонтакте.

    Атрибуты:
        access_token: Пользовательский access token.
        group_id:     ID сообщества.

    raise:
        RuntimeError - при отсутствии пользовательского токена в настройках.
        ValueError - если ID группы не целое число.
    """

    access_token: str
    group_id: int

    @staticmethod
    def from_settings() -> "VKConfig":
        """Создаёт конфигурацию из Django settings."""
        token = getattr(settings, "VK_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError(
                "VK: отсутствует пользовательский токен(VK_ACCESS_TOKEN)."
            )

        group_id_raw = getattr(settings, "VK_GROUP_ID", 0)
        try:
            group_id = abs(int(group_id_raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("VK: VK_GROUP_ID должен быть целым числом.") from exc

        return VKConfig(access_token=token, group_id=group_id)


# =============================================================================
# Pure helpers (no VK API)
# =============================================================================


def _slugify_hashtag(text: str) -> str:
    """
    Преобразует метку к виду для хэштега: латиница/цифры/подчёркивания, в нижнем регистре.
    Пробелы → подчёркивания, прочие символы удаляются.
    """
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "", s)
    return s


def _record_artists(record: Any) -> str:
    artists_qs = getattr(record, "artists", None)
    if artists_qs and hasattr(artists_qs, "all"):
        return ", ".join(a.name for a in artists_qs.all())
    return "Неизвестный исполнитель"


def _record_cover_path(record: Any) -> Path | None:
    cover = getattr(record, "cover_image", None)
    raw = getattr(cover, "path", None) if cover else None
    if not raw:
        return None
    p = Path(raw)
    return p if p.exists() else None


def _get_release_date(record: Any) -> date | None:
    """Безопасно извлекает дату релиза из record."""
    try:
        value = record.get_release_date()
    except AttributeError:
        logger.warning("VK: record не имеет get_release_date()")
        return None
    except (TypeError, ValueError) as exc:
        logger.debug("VK: get_release_date() вернуло неверные данные: %s", exc)
        return None

    if not isinstance(value, date):
        logger.debug("VK: get_release_date() вернуло не date: %r", value)
        return None

    return value


def _format_release_date(record: Any) -> str | None:
    """Форматирует дату релиза для вывода."""
    release_date = _get_release_date(record)
    if release_date is None:
        return None

    en_month = month_abbr[release_date.month]
    return f"{en_month} {release_date.day}, {release_date.year}"


def _format_record_format(record: Any) -> str | None:
    """
    Формирует строку «Format» по record.formats:
      • если встречается 7"/10"/12" — '<size> Vinyl';
      • иначе берём первые 1–2 уникальных названия, склеенных « / ».
    """
    names: list[str] = []
    fm = getattr(record, "formats", None)

    if fm is not None:
        try:
            if hasattr(fm, "values_list"):
                names = list(fm.values_list("name", flat=True))
            elif hasattr(fm, "all"):
                names = [getattr(f, "name", "") for f in fm.all()]
        except (AttributeError, TypeError) as exc:
            logger.debug("VK: не удалось получить список форматов: %s", exc)
            names = []

    names = [n for n in names if n]
    size = next((n for n in names if n in {'7"', '10"', '12"'}), None)
    if size:
        return f"{size} Vinyl"
    if names:
        uniq: list[str] = []
        for n in names:
            if n not in uniq:
                uniq.append(n)
        return " / ".join(uniq[:2])
    return None


def _build_hashtags(record: Any) -> str:
    """
    Формирует строку хэштегов из record.genres и record.styles.

    Для каждого значения добавляет:
      - #ds_<slug_without_ds_prefix>
      - #<slug_without_ds_prefix>

    То есть у второй метки префикса ds_ нет, но хэштег (#) остаётся.
    """

    def names(qs_name: str) -> Iterable[str]:
        qs = getattr(record, qs_name, None)
        if qs is not None and hasattr(qs, "values_list"):
            for name in qs.values_list("name", flat=True):
                yield str(name)

    raw: list[str] = []

    for n in names("genres"):
        s = _slugify_hashtag(n)
        if not s:
            continue
        plain = s[3:] if s.startswith("ds_") else s
        raw.extend((f"ds_{plain}", plain))

    for n in names("styles"):
        s = _slugify_hashtag(n)
        if not s:
            continue
        plain = s[3:] if s.startswith("ds_") else s
        raw.extend((f"ds_{plain}", plain))

    out: list[str] = []
    seen: set[str] = set()
    for t in raw:
        if t not in seen:
            out.append("#" + t)
            seen.add(t)

    return " ".join(out)


def compose_record_text(record: Any) -> str:
    """
    Собирает текст поста строго как в требовании:

    <price condition availability>
    <Artists> — <Title>
    Label: <Label> – <Catalog>
    Format: <Format>            # если нет данных — оставляем пусто после двоеточия
    Release Date: <Date>        # если нет данных — оставляем пусто после двоеточия

    (пустая строка)
    <хэштеги>
    """
    title: str = getattr(record, "title", "Без названия")
    artists = _record_artists(record)

    label_obj = getattr(record, "label", None)
    label_name = getattr(label_obj, "name", "") if label_obj else ""
    catalog_number = getattr(record, "catalog_number", "") or ""

    fmt = _format_record_format(record) or ""
    release = _format_release_date(record) or ""

    price = getattr(record, "price", "")
    condition = getattr(record, "condition", "")
    availability = getattr(record, "availability_status", "")

    if hasattr(record, "get_condition_display"):
        condition = record.get_condition_display()

    if hasattr(record, "get_availability_status_display"):
        availability = record.get_availability_status_display()

    header_parts = [str(word) for word in (price, condition, availability) if word]
    first_line = " ".join(header_parts)

    lines: list[str] = [first_line]
    lines.append("")

    lines.append(f"{artists} — {title}")

    if catalog_number:
        sep = " – "
        label_line = (
            f"Label: {sep.join([label_name, catalog_number])}"
            if label_name
            else f"Label: {catalog_number}"
        )
    else:
        label_line = f"Label: {label_name}" if label_name else "Label:"
    lines.append(label_line)

    lines.append(f"Format: {fmt}")
    lines.append(f"Release Date: {release}")

    hashtags = _build_hashtags(record)
    if hashtags:
        lines.append("")
        lines.append("")
        lines.append(hashtags)

    return "\n".join(lines)


def _render_record_message(record: Record, message_template: str | None) -> str:
    if not message_template:
        return compose_record_text(record)

    artists = _record_artists(record)
    label_name = getattr(getattr(record, "label", None), "name", "-")
    return message_template.format(
        title=getattr(record, "title", ""),
        artists=artists,
        label=label_name,
        catalog_number=getattr(record, "catalog_number", ""),
        price=getattr(record, "price", None),
        stock=getattr(record, "stock", None),
    )


# =============================================================================
# VK service
# =============================================================================


class VKService:
    """
    Сервис публикации в сообщество ВКонтакте.

    — Авторизация по пользовательскому токену.
    — Публикация текста/фото/опц. аудио.
    — Компоновка текста постов для Record.
    """

    def __init__(self, config: VKConfig):
        self._config = config
        self._vk = vk_api.VkApi(token=config.access_token)

    @classmethod
    def from_settings(cls) -> "VKService":
        return cls(VKConfig.from_settings())

    @property
    def owner_id(self) -> int:
        """Отрицательный owner_id сообщества (требование VK API)."""
        return -abs(self._config.group_id)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def post_record_with_audio(
        self, record: Record, message_template: str | None = None
    ) -> int:
        """
        Публикует релиз с обложкой и, по возможности, с MP3-превью треков.
        """
        message = _render_record_message(record, message_template)
        all_attachments = self._collect_release_attachments(record, with_audio=True)

        logger.info(
            "VK: публикую релиз (record_id=%s) с %d вложениями: %s",
            getattr(record, "pk", None),
            len(all_attachments),
            ",".join(all_attachments) if all_attachments else "—",
        )
        return self._wall_post(message=message, attachments=all_attachments or None)

    def post_record_with_playlist(
        self, record: Record, message_template: str | None = None
    ) -> int:
        """
        Публикует релиз, но вместо списка аудио прикрепляет плейлист.
        Алгоритм:
          - загрузить обложку (photo)
          - загрузить mp3-превью треков (audio)
          - создать плейлист у текущего пользователя
          - добавить аудио в плейлист
          - запостить на стену: [photo, audio_playlist...]
        Если плейлист создать/наполнить не удалось — фоллбэк на post_record_with_audio().
        """
        message = _render_record_message(record, message_template)

        # 1) Обложка (желательно иметь, т.к. VK часто требует фото для аудио-вложений)
        photo_attachment: str | None = None
        cover_path = _record_cover_path(record)
        if cover_path:
            photo_attachment = self._upload_photo(cover_path)

        # 2) Загружаем аудио (превью треков)
        audio_attachments: list[str] = []
        tracks = getattr(record, "tracks", None)

        if tracks is not None and hasattr(tracks, "all"):
            artists = _record_artists(record)
            audio_qs = (
                tracks.filter(audio_preview__isnull=False)
                .exclude(audio_preview="")
                .order_by("position_index")
            )

            # Для плейлиста можно больше, чем 10, но чтобы не менять поведение/нагрузку резко — ограничим.
            # При необходимости потом увеличим/снимем лимит.
            max_upload = 30

            for track in audio_qs[:max_upload]:
                preview = getattr(track, "audio_preview", None)
                p = Path(getattr(preview, "path", "")) if preview else None
                if p and p.exists():
                    att = self._upload_audio(p, artists, getattr(track, "title", ""))
                    if att:
                        audio_attachments.append(att)
                else:
                    logger.warning(
                        "VK: нет mp3 на диске для трека #%s релиза #%s — трек пропущен.",
                        getattr(track, "pk", None),
                        getattr(record, "pk", None),
                    )

        # Если аудио вообще нет — плейлист не из чего делать
        if not audio_attachments:
            logger.warning(
                "VK: для релиза #%s не удалось загрузить ни одного аудио-превью — публикую обычным методом.",
                getattr(record, "pk", None),
            )
            return self.post_record_with_audio(
                record, message_template=message_template
            )

        # 3) Создаём плейлист + добавляем туда аудио
        try:
            playlist_owner_id = self._get_current_user_id()

            playlist_title = (
                f"{_record_artists(record)} — {getattr(record, 'title', '')}".strip(
                    " —"
                )
            )
            playlist_id, playlist_access_key = self._create_playlist(
                owner_id=playlist_owner_id,
                title=playlist_title or "New playlist",
            )

            audio_ids = [self._audio_attachment_to_id(a) for a in audio_attachments]
            audio_ids = [x for x in audio_ids if x]

            self._add_audios_to_playlist(
                owner_id=playlist_owner_id,
                playlist_id=playlist_id,
                audio_ids=audio_ids,
            )

            playlist_attachment = f"audio_playlist{playlist_owner_id}_{playlist_id}"
            if playlist_access_key:
                playlist_attachment += f"_{playlist_access_key}"

        except ApiError as e:
            logger.warning(
                "VK: не удалось создать/наполнить плейлист (ApiError). Фоллбэк на обычный пост: %s",
                e,
            )
            return self.post_record_with_audio(
                record, message_template=message_template
            )
        except Exception as e:
            logger.exception(
                "VK: не удалось создать/наполнить плейлист. Фоллбэк на обычный пост: %s",
                e,
            )
            return self.post_record_with_audio(
                record, message_template=message_template
            )

        # 4) Постим: обложка (если есть) + плейлист
        attachments_for_post: list[str] = []
        if photo_attachment:
            attachments_for_post.append(photo_attachment)
        else:
            logger.warning(
                "VK: релиз #%s публикуется с плейлистом без обложки. "
                "Если VK откажет — добавь/проверь cover_image.",
                getattr(record, "pk", None),
            )

        attachments_for_post.append(playlist_attachment)

        logger.info(
            "VK: публикую релиз (record_id=%s) плейлистом. attachments=%s",
            getattr(record, "pk", None),
            ",".join(attachments_for_post),
        )
        return self._wall_post(message=message, attachments=attachments_for_post)

    def _get_current_user_id(self) -> int:
        """
        Возвращает id пользователя, от имени которого работает токен.
        Кэширует значение в инстансе.
        """
        cached = getattr(self, "_current_user_id", None)
        if isinstance(cached, int) and cached > 0:
            return cached

        data = self._vk.method("users.get", {})
        user_id = int(data[0]["id"])
        setattr(self, "_current_user_id", user_id)
        return user_id

    @staticmethod
    def _audio_attachment_to_id(attachment: str) -> str | None:
        """
        'audio123_456' -> '123_456'
        """
        if not attachment.startswith("audio"):
            return None
        return attachment[len("audio") :]

    def _create_playlist(self, owner_id: int, title: str) -> tuple[int, str | None]:
        """
        Создаёт плейлист. Возвращает (playlist_id, access_key|None).
        """
        resp: Dict[str, Any] = self._vk.method(
            "audio.createPlaylist",
            {"owner_id": owner_id, "title": title},
        )

        playlist: Dict[str, Any] = resp.get("playlist", resp)
        playlist_id = (
            playlist.get("id")
            or playlist.get("playlist_id")
            or playlist.get("album_id")
        )
        if not playlist_id:
            raise ValueError(
                f"VK: audio.createPlaylist вернул неожиданный ответ: {resp!r}"
            )

        access_key = playlist.get("access_key")
        return int(playlist_id), str(access_key) if access_key else None

    def _add_audios_to_playlist(
        self, owner_id: int, playlist_id: int, audio_ids: list[str]
    ) -> None:
        """
        Добавляет аудио в плейлист. Шлём чанками, чтобы не упереться в лимит длины параметра.
        """
        if not audio_ids:
            return

        chunk_size = 50
        for i in range(0, len(audio_ids), chunk_size):
            chunk = audio_ids[i : i + chunk_size]
            self._vk.method(
                "audio.addToPlaylist",
                {
                    "owner_id": owner_id,
                    "playlist_id": playlist_id,
                    "audios": ",".join(chunk),
                },
            )

    # -------------------------------------------------------------------------
    # High-level internal helpers
    # -------------------------------------------------------------------------

    def _collect_release_attachments(
        self, record: Record, *, with_audio: bool
    ) -> list[str]:
        """
        Собирает вложения релиза:
        - пытается прикрепить обложку (photo)
        - при with_audio=True пытается прикрепить превью треков (audio) с лимитом VK
        - если аудио есть, но фото нет — удаляет аудио (требование VK)
        """
        attachments: list[str] = []

        # 1) фото
        cover_path = _record_cover_path(record)
        if cover_path:
            photo = self._upload_photo(cover_path)
            if photo:
                attachments.append(photo)
        else:
            logger.warning(
                "VK: для записи #%s обложка недоступна (файл отсутствует). "
                "Если будут аудио-вложения, они будут удалены из-за требований VK.",
                getattr(record, "pk", None),
            )

        if not with_audio:
            return attachments

        # 2) аудио
        audio_attachments: list[str] = []
        tracks = getattr(record, "tracks", None)

        if tracks is not None and hasattr(tracks, "all"):
            artists = _record_artists(record)

            audio_qs = (
                tracks.filter(audio_preview__isnull=False)
                .exclude(audio_preview="")
                .order_by("position_index")
            )

            # лимит VK: 10 вложений; если фото есть — оставляем 1 под фото
            limit = 10 - (1 if attachments else 0)

            for track in audio_qs[: max(0, limit)]:
                preview = getattr(track, "audio_preview", None)
                p = Path(getattr(preview, "path", "")) if preview else None

                if p and p.exists():
                    att = self._upload_audio(p, artists, getattr(track, "title", ""))
                    if att:
                        audio_attachments.append(att)
                else:
                    logger.warning(
                        "VK: у записи #%s отсутствует mp3-файл на диске для трека #%s — трек пропущен.",
                        getattr(record, "pk", None),
                        getattr(track, "pk", None),
                    )

        # 3) требование VK: если есть аудио — обязано быть фото
        if audio_attachments and not attachments:
            logger.warning(
                "VK: у записи #%s есть аудио-вложения, но нет фото — аудио будут удалены из публикации "
                "(требование VK: нужна хотя бы одна фотография для аудио).",
                getattr(record, "pk", None),
            )
            audio_attachments.clear()

        return attachments + audio_attachments

    # -------------------------------------------------------------------------
    # VK API low-level (photos / audio / wall)
    # -------------------------------------------------------------------------

    def _get_wall_upload_url(self) -> str:
        """Возвращает upload_url для загрузки фото на стену сообщества."""
        data: dict[str, Any] = self._vk.method(
            "photos.getWallUploadServer", {"group_id": abs(self._config.group_id)}
        )
        return str(data["upload_url"])

    def _save_wall_photo(self, upload_resp: dict[str, Any]) -> dict[str, Any]:
        """Сохраняет фотографию, загруженную через upload_url стены."""
        saved_list = self._vk.method(
            "photos.saveWallPhoto",
            {
                "group_id": abs(self._config.group_id),
                "photo": upload_resp["photo"],
                "server": upload_resp["server"],
                "hash": upload_resp["hash"],
            },
        )
        return saved_list[0]

    def _upload_photo(self, image_path: Path) -> str | None:
        """
        Загружает фото на стену и возвращает строку вложения 'photo<owner_id>_<id>'.
        При ошибке возвращает None.
        """
        try:
            url = self._get_wall_upload_url()
        except ApiError as e:
            logger.error("VK: не удалось получить upload_url для фото: %s", e)
            return None

        try:
            with image_path.open("rb") as f:
                resp = requests.post(
                    url, files={"photo": (image_path.name, f, "image/jpeg")}, timeout=30
                )
                resp.raise_for_status()
            upload_resp = resp.json()
        except requests.RequestException as e:
            logger.exception("VK: ошибка HTTP при загрузке фото: %s", e)
            return None

        try:
            saved = self._save_wall_photo(upload_resp)
            return f"photo{saved['owner_id']}_{saved['id']}"
        except ApiError as e:
            logger.error("VK: ошибка сохранения фото (photos.saveWallPhoto): %s", e)
            return None

    def _get_audio_upload_url(self) -> str:
        """Возвращает upload_url для загрузки аудио (если аудио разрешено в конфигурации)."""
        data: dict[str, Any] = self._vk.method("audio.getUploadServer", {})
        return str(data["upload_url"])

    def _save_audio(
        self, upload_resp: dict[str, Any], artist: str, title: str
    ) -> dict[str, Any]:
        """Сохраняет аудио, загруженное через upload_url."""
        return self._vk.method(
            "audio.save",
            {
                "audio": upload_resp["audio"],
                "server": upload_resp["server"],
                "hash": upload_resp["hash"],
                "artist": artist,
                "title": title,
            },
        )

    def _upload_audio(self, audio_path: Path, artist: str, title: str) -> str | None:
        """
        Загружает MP3 и возвращает 'audio<owner_id>_<id>' или None, если Audio API недоступен.
        """
        try:
            url = self._get_audio_upload_url()
        except ApiError as e:
            code = getattr(e, "code", None)
            if code == 270:
                logger.warning(
                    "VK: Audio API отключён для приложения (код 270). Аудио пропущено."
                )
            else:
                logger.warning("VK: не удалось получить upload_url для аудио: %s", e)
            return None

        try:
            with audio_path.open("rb") as f:
                resp = requests.post(
                    url, files={"file": (audio_path.name, f, "audio/mpeg")}, timeout=60
                )
                resp.raise_for_status()
            upload_resp = resp.json()
        except requests.RequestException as e:
            logger.exception("VK: ошибка HTTP при загрузке аудио: %s", e)
            return None

        try:
            saved = self._save_audio(upload_resp, artist, title)
            return f"audio{saved['owner_id']}_{saved['id']}"
        except ApiError as e:
            logger.warning("VK: ошибка audio.save, аудио пропущено: %s", e)
            return None

    def _wall_post(self, message: str, attachments: Sequence[str] | None = None) -> int:
        """
        Вызов VK API wall.post и получение post_id опубликованной записи.
        attachments — список attachment-строк вида "photo{owner_id}_{media_id}" и т.п.
        """
        publish_date = int(time.time()) + POSTPONE_DELAY_SECONDS

        attach_param: str | None
        if attachments:
            attach_param = ",".join(att for att in attachments if att)
        else:
            attach_param = None

        resp: Dict[str, Any] = self._vk.method(
            "wall.post",
            {
                "owner_id": self.owner_id,
                "message": message,
                "attachments": attach_param,
                "from_group": 1,
                "publish_date": publish_date,
            },
        )

        post_id_raw = resp.get("post_id")
        if not isinstance(post_id_raw, int):
            logger.error(
                "VK: wall.post вернул неожиданный post_id: %r, ответ: %r",
                post_id_raw,
                resp,
            )
            raise ValueError(
                f"VK: wall.post вернул некорректный post_id: {post_id_raw!r}"
            )

        logger.info("VK: запись опубликована, post_id=%s.", post_id_raw)
        return post_id_raw

# apps/records/services/social/vk_service.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
import vk_api
from django.conf import settings
from vk_api.exceptions import ApiError

logger = logging.getLogger(__name__)


# ======================================================================================
# Конфигурация
# ======================================================================================

@dataclass(frozen=True)
class VKConfig:
    """
    Конфигурация доступа к API ВКонтакте.

    Используется ТОЛЬКО пользовательский токен standalone-приложения и id группы.

    Атрибуты:
        access_token: Пользовательский access token (scopes: wall, photos, groups, offline; аудио — при наличии).
        group_id:     Положительный ID сообщества (без минуса).
        enable_audio: Разрешать ли попытку загрузки MP3 через Audio API (по умолчанию True).
                      Если у приложения аудио отключено — загрузка будет молча пропущена с предупреждением в логе.
    """

    access_token: str
    group_id: int
    enable_audio: bool = True

    @staticmethod
    def from_settings() -> "VKConfig":
        """
        Создаёт конфигурацию из Django settings.

        Требуемые настройки:
            VK_ACCESS_TOKEN: str — пользовательский токен standalone.
            VK_GROUP_ID:     int — положительный ID сообщества.
            VK_ENABLE_AUDIO: bool (опц.) — включать ли загрузку MP3 (по умолчанию True).

        Raises:
            RuntimeError | ValueError: при некорректных значениях.
        """
        token = getattr(settings, "VK_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError("VK: отсутствует VK_ACCESS_TOKEN (ожидается пользовательский токен).")

        group_id_raw = getattr(settings, "VK_GROUP_ID", 0)
        try:
            group_id = int(group_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("VK: VK_GROUP_ID должен быть целым числом.") from exc
        if group_id <= 0:
            raise ValueError("VK: VK_GROUP_ID должен быть положительным (без минуса).")

        enable_audio = bool(getattr(settings, "VK_ENABLE_AUDIO", True))
        return VKConfig(access_token=token, group_id=group_id, enable_audio=enable_audio)


# ======================================================================================
# Утилиты форматирования текста поста
# ======================================================================================

def _slugify_hashtag(text: str) -> str:
    """
    Преобразует метку к виду для хэштега: латиница/цифры/подчёркивания, в нижнем регистре.
    Пробелы → подчёркивания, прочие символы удаляются.
    """
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "", s)
    return s


def _format_release_date(record: Any) -> Optional[str]:
    """
    Возвращает дату релиза в формате 'Mon d, YYYY' с английскими
    аббревиатурами месяцев (Jan, Feb, Mar, ...). Если данных нет — None.
    """
    month_en = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
    }

    # Пытаемся взять готовую дату из метода модели, если он есть.
    try:
        get_date = getattr(record, "get_release_date", None)
        d: Optional[date] = get_date() if callable(get_date) else None
        if d:
            mon = month_en.get(d.month)
            if mon:
                return f"{mon} {d.day}, {d.year}"
    except Exception:
        pass

    # Собираем из частей (год/месяц/день), если доступны.
    y = getattr(record, "release_year", None)
    m = getattr(record, "release_month", None)
    dd = getattr(record, "release_day", None)

    if y and m and dd:
        mon = month_en.get(int(m))
        if mon:
            return f"{mon} {int(dd)}, {int(y)}"
    if y and m:
        mon = month_en.get(int(m))
        if mon:
            return f"{mon}, {int(y)}"
    if y:
        return f"{int(y)}"
    return None


def _format_record_format(record: Any) -> Optional[str]:
    """
    Формирует человекочитаемое значение «Format» на основе record.formats.

    Если встречается размер 7"/10"/12" — возвращает '<size> Vinyl', иначе
    возвращает первые 1–2 уникальных названия форматов, склеенных « / ».
    """
    names: List[str] = []
    fm = getattr(record, "formats", None)

    try:
        if fm is not None and hasattr(fm, "values_list"):
            names = list(fm.values_list("name", flat=True))
        elif fm is not None and hasattr(fm, "all"):
            names = [getattr(f, "name", "") for f in fm.all()]
    except Exception:
        names = []

    names = [n for n in names if n]
    size = next((n for n in names if n in {'7"', '10"', '12"'}), None)
    if size:
        return f'{size} Vinyl'
    if names:
        uniq: List[str] = []
        for n in names:
            if n not in uniq:
                uniq.append(n)
        return " / ".join(uniq[:2])
    return None


def _build_hashtags(record: Any) -> str:
    """
    Формирует строку хэштегов из record.genres и record.styles.

    Для каждого значения добавляет `#ds_<slug>` и `#<slug>`. Возвращает строку
    с пробелами между тегами или пустую строку.
    """
    def names(qs_name: str) -> Iterable[str]:
        qs = getattr(record, qs_name, None)
        if qs is not None and hasattr(qs, "values_list"):
            for n in qs.values_list("name", flat=True):
                yield str(n)

    raw: List[str] = []
    for n in (names("genres") or []):
        s = _slugify_hashtag(n)
        if s:
            raw.extend((f"ds_{s}", s))
    for n in (names("styles") or []):
        s = _slugify_hashtag(n)
        if s:
            raw.extend((f"ds_{s}", s))

    out, seen = [], set()
    for t in raw:
        if t not in seen:
            out.append("#" + t)
            seen.add(t)
    return " ".join(out)


def compose_record_text(record: Any) -> str:
    """
    Собирает текст поста строго как в требовании:

    В НАЛИЧИИ
    <Artists> — <Title>
    Label: <Label> – <Catalog>
    Format: <Format>            # если нет данных — оставляем пусто после двоеточия
    Release Date: <Date>        # если нет данных — оставляем пусто после двоеточия

    (пустая строка)
    <хэштеги>
    """
    title: str = getattr(record, "title", "Без названия")
    artists_qs = getattr(record, "artists", None)
    artists = (
        ", ".join(a.name for a in artists_qs.all())
        if artists_qs and hasattr(artists_qs, "all")
        else "Неизвестный исполнитель"
    )

    label_obj = getattr(record, "label", None)
    label_name = getattr(label_obj, "name", "") if label_obj else ""
    catalog_number = getattr(record, "catalog_number", "") or ""

    # формат и дата — можем не знать, но строки обязаны быть
    fmt = _format_record_format(record) or ""
    release = _format_release_date(record) or ""

    # первая строка — всегда фиксированная
    lines: List[str] = ["В НАЛИЧИИ"]

    # вторая — артист — тайтл
    lines.append(f"{artists} — {title}")

    # третья — Label: <лейбл> – <каталожник>, если чего-то нет — просто Label:
    label_parts: List[str] = []
    if label_name:
        label_parts.append(label_name)
    if catalog_number:
        # en dash, как в образце
        sep = " – "
        label_line = f"Label: {sep.join([label_name, catalog_number])}" if label_name else f"Label: {catalog_number}"
    else:
        label_line = f"Label: {label_name}" if label_name else "Label:"
    lines.append(label_line)

    # четвёртая — Format: (всегда печатаем ключ)
    lines.append(f"Format: {fmt}")

    # пятая — Release Date: (всегда печатаем ключ)
    lines.append(f"Release Date: {release}")

    # хэштеги (если есть) — после пустой строки
    hashtags = _build_hashtags(record)
    if hashtags:
        lines.append("")
        lines.append(hashtags)

    return "\n".join(lines)


# ======================================================================================
# Сервис VK
# ======================================================================================

class VKService:
    """
    Сервис публикации в сообщество ВКонтакте.

    — Авторизация по пользовательскому токену.
    — Публикация текста/фото/опц. аудио.
    — Компоновка текста постов для Record/Track.
    """

    def __init__(self, config: VKConfig):
        """
        Инициализирует сервис.

        Args:
            config: Конфигурация доступа.
        """
        self._config = config
        self._vk = vk_api.VkApi(token=config.access_token)

    @classmethod
    def from_settings(cls) -> "VKService":
        """Создаёт сервис, считывая настройки из Django settings."""
        return cls(VKConfig.from_settings())

    @property
    def owner_id(self) -> int:
        """Отрицательный owner_id сообщества (требование VK API)."""
        return -abs(self._config.group_id)

    # -------------------------- низкоуровневые вызовы --------------------------

    def _get_wall_upload_url(self) -> str:
        """Возвращает upload_url для загрузки фото на стену сообщества."""
        data: Dict[str, Any] = self._vk.method(
            "photos.getWallUploadServer", {"group_id": abs(self._config.group_id)}
        )
        return str(data["upload_url"])

    def _save_wall_photo(self, upload_resp: Dict[str, Any]) -> Dict[str, Any]:
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

    def _upload_photo(self, image_path: Path) -> Optional[str]:
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
                resp = requests.post(url, files={"photo": (image_path.name, f, "image/jpeg")}, timeout=30)
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
        data: Dict[str, Any] = self._vk.method("audio.getUploadServer", {})
        return str(data["upload_url"])

    def _save_audio(self, upload_resp: Dict[str, Any], artist: str, title: str) -> Dict[str, Any]:
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

    def _upload_audio(self, audio_path: Path, artist: str, title: str) -> Optional[str]:
        """
        Загружает MP3 и возвращает 'audio<owner_id>_<id>' или None, если Audio API недоступен.
        """
        if not self._config.enable_audio:
            logger.info("VK: загрузка аудио отключена настройкой VK_ENABLE_AUDIO=False.")
            return None

        try:
            url = self._get_audio_upload_url()
        except ApiError as e:
            code = getattr(e, "code", None)
            if code == 270:
                logger.warning("VK: Audio API отключён для приложения (код 270). Аудио пропущено.")
            else:
                logger.warning("VK: не удалось получить upload_url для аудио: %s", e)
            return None

        try:
            with audio_path.open("rb") as f:
                resp = requests.post(url, files={"file": (audio_path.name, f, "audio/mpeg")}, timeout=60)
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

    def _wall_post(self, message: str, attachments: Optional[List[str]] = None) -> int:
        """Вызывает wall.post и возвращает post_id."""
        attach = ",".join(attachments) if attachments else None
        resp: Dict[str, Any] = self._vk.method(
            "wall.post",
            {
                "owner_id": self.owner_id,
                "message": message,
                "attachment": attach,
                "from_group": 1,
            },
        )
        post_id = int(resp.get("post_id"))
        logger.info("VK: запись опубликована, post_id=%s.", post_id)
        return post_id

    # ------------------------------ публичные методы ------------------------------

    def health_check(self) -> bool:
        """
        Базовая проверка: доступ к группе и возможность получить upload_url для стены.
        """
        ok = True
        try:
            self._vk.method("groups.getById", {"group_id": abs(self._config.group_id)})
            logger.debug("VK: groups.getById — ОК.")
        except ApiError as e:
            ok = False
            logger.error("VK: groups.getById — ошибка: %s", e)

        if ok:
            try:
                self._get_wall_upload_url()
                logger.debug("VK: photos.getWallUploadServer — ОК (пользовательский токен).")
            except ApiError as e:
                ok = False
                logger.error("VK: photos.getWallUploadServer — ошибка: %s", e)

        logger.info("VK: health-check %s.", "успешен" if ok else "с ошибками")
        return ok

    def post_text(self, message: str) -> int:
        """Публикует текстовую запись (без вложений)."""
        logger.info("VK: публикую текстовую запись.")
        return self._wall_post(message=message)

    def post_with_image(self, message: str, image_path: str | Path) -> int:
        """
        Публикует запись с одним изображением (обложка).
        При отсутствии файла — публикует только текст.
        """
        path = Path(image_path)
        if not path.exists():
            logger.warning("VK: изображение не найдено (%s). Публикую только текст.", path)
            return self.post_text(message)

        photo = self._upload_photo(path)
        return self._wall_post(message=message, attachments=[photo] if photo else None)

    # ------------------------------ доменная логика ------------------------------

    def post_record(self, record: Any, *, message_template: Optional[str] = None) -> int:
        """
        Публикует «релиз» (Record). Текст — по шаблону или compose_record_text().
        При наличии локальной обложки — прикрепляет её.
        """
        if message_template:
            # минимальный набор плейсхолдеров для совместимости
            artists_qs = getattr(record, "artists", None)
            artists = ", ".join(a.name for a in artists_qs.all()) if artists_qs and hasattr(artists_qs, "all") else "Неизвестный исполнитель"
            label_name = getattr(getattr(record, "label", None), "name", "-")
            message = message_template.format(
                title=getattr(record, "title", ""),
                artists=artists,
                label=label_name,
                catalog_number=getattr(record, "catalog_number", ""),
                price=getattr(record, "price", None),
                stock=getattr(record, "stock", None),
            )
        else:
            message = compose_record_text(record)

        cover = getattr(record, "cover_image", None)
        cover_path = getattr(cover, "path", None) if cover else None
        return self.post_with_image(message, cover_path) if cover_path else self.post_text(message)

    def post_record_with_audio(self, record: Any, *, message_template: Optional[str] = None) -> int:
        """
        Публикует релиз с обложкой и, по возможности, с MP3-превью треков.
        Лимит ВК — до 10 вложений; оставляем 1 под обложку и до 9 аудио.
        """
        # 1) текст поста (строго по новому компоновщику, либо по шаблону)
        if message_template:
            artists_qs = getattr(record, "artists", None)
            artists = (
                ", ".join(a.name for a in artists_qs.all())
                if artists_qs and hasattr(artists_qs, "all")
                else "Неизвестный исполнитель"
            )
            label_name = getattr(getattr(record, "label", None), "name", "")
            message = message_template.format(
                title=getattr(record, "title", ""),
                artists=artists,
                label=label_name,
                catalog_number=getattr(record, "catalog_number", ""),
            )
        else:
            message = compose_record_text(record)

        attachments: List[str] = []

        # 2) обложка
        cover = getattr(record, "cover_image", None)
        cover_path = Path(getattr(cover, "path", "")) if cover and getattr(cover, "path", "") else None
        if cover_path and cover_path.exists():
            photo = self._upload_photo(cover_path)
            if photo:
                attachments.append(photo)

        # 3) аудио (по возможности)
        tracks = getattr(record, "tracks", None)
        if tracks is not None and hasattr(tracks, "all"):
            artists_qs = getattr(record, "artists", None)
            artists = (
                ", ".join(a.name for a in artists_qs.all())
                if artists_qs and hasattr(artists_qs, "all")
                else "Неизвестный исполнитель"
            )
            audio_qs = tracks.exclude(audio_preview="").order_by("position_index")
            limit = 10 - (1 if attachments else 0)
            for track in audio_qs[:max(0, limit)]:
                preview = getattr(track, "audio_preview", None)
                p = Path(getattr(preview, "path", "")) if preview else None
                if p and p.exists():
                    att = self._upload_audio(p, artists, getattr(track, "title", ""))
                    if att:
                        attachments.append(att)

        # 4) публикация
        return self._wall_post(message=message, attachments=attachments if attachments else None)

    def post_track(self, track: Any, *, message_template: Optional[str] = None) -> int:
        """
        Публикует отдельный трек (Track). Прикрепляет обложку записи и (опц.) аудио-превью.
        """
        # текст
        if message_template:
            record = getattr(track, "record", None)
            artists_qs = getattr(record, "artists", None) if record else None
            artists = ", ".join(a.name for a in artists_qs.all()) if artists_qs and hasattr(artists_qs, "all") else "Неизвестный исполнитель"
            label_name = getattr(getattr(record, "label", None), "name", "-") if record else "-"
            message = message_template.format(
                track_title=getattr(track, "title", ""),
                track_position=getattr(track, "position", ""),
                track_duration=getattr(track, "duration", ""),
                record_title=getattr(record, "title", "") if record else "",
                artists=artists,
                label=label_name,
                catalog_number=getattr(record, "catalog_number", "") if record else "",
            )
        else:
            record = getattr(track, "record", None)
            artists_qs = getattr(record, "artists", None) if record else None
            artists = ", ".join(a.name for a in artists_qs.all()) if artists_qs and hasattr(artists_qs, "all") else "Неизвестный исполнитель"
            parts: List[str] = []
            header = f"{artists} — {getattr(track, 'title', 'Без названия')}"
            pos = getattr(track, "position", "")
            dur = getattr(track, "duration", "")
            if pos:
                header = f"[{pos}] {header}"
            if dur:
                header = f"{header} ({dur})"
            parts.append(header)

            if record:
                parts.append(f"💿 {getattr(record, 'title', '')}")
                label = getattr(getattr(record, "label", None), "name", "-")
                cat = getattr(record, "catalog_number", "-") or "-"
                meta = " • ".join([p for p in (f"🏷 {label}" if label != "-" else "", f"📋 {cat}" if cat != "-" else "") if p])
                if meta:
                    parts.append(meta)

            yt = getattr(track, "youtube_url", None)
            if yt:
                parts.append(f"\n🎧 Превью: {yt}")

            message = "\n".join(parts)

        # вложения
        attachments: List[str] = []

        # обложка
        record = getattr(track, "record", None)
        cover = getattr(record, "cover_image", None) if record else None
        cover_path = Path(getattr(cover, "path", "")) if cover and getattr(cover, "path", "") else None
        if cover_path and cover_path.exists():
            photo = self._upload_photo(cover_path)
            if photo:
                attachments.append(photo)

        # аудио
        preview = getattr(track, "audio_preview", None)
        p = Path(getattr(preview, "path", "")) if preview else None
        if p and p.exists():
            artists_qs = getattr(record, "artists", None) if record else None
            artists = ", ".join(a.name for a in artists_qs.all()) if artists_qs and hasattr(artists_qs, "all") else "Неизвестный исполнитель"
            att = self._upload_audio(p, artists, getattr(track, "title", ""))
            if att:
                attachments.append(att)

        return self._wall_post(message=message, attachments=attachments if attachments else None)

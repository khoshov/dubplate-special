from __future__ import annotations

import logging
import re
import time
from calendar import month_abbr
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence

import requests
import vk_api
from django.conf import settings
from vk_api.exceptions import ApiError

from config.logging import NOTICE_LEVEL, build_log_extra, log_event
from records.models import AvailableChoices, Record
from records.services.record_assembly import get_structured_format_incomplete_error

logger = logging.getLogger(__name__)
_VK_SERVICE_COMPONENT = "vk_service"
_VINYL_SIZE_FORMATS = {'7"', '10"', '12"'}


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


@dataclass(frozen=True)
class VKAttachmentCollectionResult:
    attachments: list[str]
    photo_expected: bool
    photo_uploaded: bool
    audio_expected_count: int
    audio_uploaded_count: int
    audio_failed_count: int
    failed_track_titles: list[str]
    audio_failure_details: list[dict[str, str]]


@dataclass(frozen=True)
class VKPublicationResult:
    post_id: int
    attachments: list[str]
    photo_expected: bool
    photo_uploaded: bool
    audio_expected_count: int
    audio_uploaded_count: int
    audio_failed_count: int
    failed_track_titles: list[str]
    audio_failure_details: list[dict[str, str]]


@dataclass(frozen=True)
class VKPreparedPublication:
    message: str
    attachments: list[str]
    photo_expected: bool
    photo_uploaded: bool
    audio_expected_count: int
    audio_uploaded_count: int
    audio_failed_count: int
    failed_track_titles: list[str]
    audio_failure_details: list[dict[str, str]]


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


def _is_not_specified_value(text: str) -> bool:
    """
    True если значение означает «не задано / not specified».
    Сравнение делается по нормализованной строке без пробелов/подчёркиваний/дефисов.
    """
    s = (text or "").strip().lower()
    if not s:
        return True
    key = re.sub(r"[\s_-]+", "", s)
    return key in {
        "notspecified",
        "неуказано",
        "незадано",
        "нет",
        "none",
    }


def _is_preorder_value(text: str) -> bool:
    """True, если значение означает статус PREORDER/ПРЕДЗАКАЗ."""
    s = (text or "").strip().lower()
    return s in {
        AvailableChoices.PREORDER.lower(),
        "предзаказ",
    }


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
        log_event(
            logger,
            logging.WARNING,
            "Запись не имеет get_release_date().",
            component=_VK_SERVICE_COMPONENT,
            event="release_date_missing_method",
            record_id=getattr(record, "pk", None),
        )
        return None
    except (TypeError, ValueError) as exc:
        log_event(
            logger,
            logging.DEBUG,
            "get_release_date() вернул некорректные данные.",
            component=_VK_SERVICE_COMPONENT,
            event="release_date_invalid",
            record_id=getattr(record, "pk", None),
            error=str(exc),
        )
        return None

    if not isinstance(value, date):
        log_event(
            logger,
            logging.DEBUG,
            "get_release_date() вернул не date.",
            component=_VK_SERVICE_COMPONENT,
            event="release_date_not_date",
            record_id=getattr(record, "pk", None),
            value=value,
        )
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
    Формирует строку «Format»:
      • в первую очередь по structured-format строкам конкретного релиза;
      • если structured rows отсутствуют — по legacy record.formats.

    Если выбран 'Not specified' — считаем, что формата нет.
    """
    structured_variant = _record_active_structured_format(record)
    if structured_variant is not None:
        carrier = (getattr(structured_variant, "carrier", "") or "").strip()
        format_name = (getattr(structured_variant, "format_name", "") or "").strip()
        details = (getattr(structured_variant, "details", "") or "").strip()
        quantity_raw = getattr(structured_variant, "quantity", 1)
        try:
            quantity = int(quantity_raw)
        except (TypeError, ValueError):
            quantity = 1

        incomplete_error = get_structured_format_incomplete_error(
            carrier=carrier,
            quantity=quantity,
            format_name=format_name,
            details=details,
        )
        if incomplete_error is not None:
            raise ValueError(incomplete_error)

        if any((carrier, format_name, details)):
            if carrier == "Vinyl" and format_name in _VINYL_SIZE_FORMATS:
                base = (
                    f"{quantity}x{format_name} Vinyl"
                    if quantity > 1
                    else f"{format_name} Vinyl"
                )
            else:
                base = f"{carrier} {format_name}"
                if quantity > 1:
                    base = f"{quantity}x {base}"

            if details:
                base = f"{base} ({details})" if base else details

            if base:
                return base

    names: list[str] = []
    fm = getattr(record, "formats", None)

    if fm is not None:
        try:
            if hasattr(fm, "values_list"):
                names = list(fm.values_list("name", flat=True))
            elif hasattr(fm, "all"):
                names = [getattr(f, "name", "") for f in fm.all()]
        except (AttributeError, TypeError) as exc:
            log_event(
                logger,
                logging.DEBUG,
                "Не удалось получить список форматов записи.",
                component=_VK_SERVICE_COMPONENT,
                event="record_formats_failed",
                record_id=getattr(record, "pk", None),
                error=str(exc),
            )
            names = []

    # выкидываем пустые и Not specified
    names = [n for n in names if n and not _is_not_specified_value(n)]

    if names:
        uniq: list[str] = []
        for n in names:
            if n not in uniq:
                uniq.append(n)
        return " / ".join(uniq)

    return None


def _record_active_structured_format(record: Any) -> Any | None:
    """Возвращает активный structured-format вариант записи."""
    related = getattr(record, "structured_formats", None)
    if related is None:
        return None

    try:
        active_variant = getattr(record, "active_structured_format_variant", None)
        if hasattr(related, "all"):
            queryset = related.all().order_by("variant_of_format", "id")
            if active_variant is not None:
                selected = queryset.filter(variant_of_format=active_variant).first()
                if selected is not None:
                    return selected
            return queryset.first()
        variants = list(related)
        if not variants:
            return None
        if active_variant is None:
            return variants[0]
        return next(
            (
                variant
                for variant in variants
                if getattr(variant, "variant_of_format", None) == active_variant
            ),
            variants[0],
        )
    except (AttributeError, TypeError):
        return None


def _normalize_hashtag_slug(text: str) -> str:
    """
    Нормализует строку под требования хэштегов:

    - если значение 'Not specified' (или аналог) — возвращает пустую строку;
    - допускает вход как с префиксом 'ds_' так и без него;
    - в основной части удаляет все подчёркивания:
        hardcore_breakbeat -> hardcorebreakbeat
        drum_and_bass -> drumandbass
    """
    if _is_not_specified_value(text):
        return ""

    s = _slugify_hashtag(text)
    if not s:
        return ""

    # если вдруг в БД уже лежит ds_*
    plain = s[3:] if s.startswith("ds_") else s

    # на всякий случай, если slugify дал not_specified
    if plain in {"not_specified", "notspecified"}:
        return ""

    # подчёркивания допускаются только после 'ds'
    plain = plain.replace("_", "")

    # финальная защита
    if plain == "notspecified":
        return ""

    return plain


def _build_hashtags(record: Any) -> str:
    """
    Формирует строку хэштегов из record.genres и record.styles.

    Правила:
      - если выбран 'Not specified' — ничего не добавляем
      - '_' только после 'ds'
      - в основной части '_' удаляются: hardcore_breakbeat -> hardcorebreakbeat

    Для каждого значения добавляет:
      - #ds_<tag>
      - #<tag>

    Дубликаты убираются.
    """

    def names(qs_name: str) -> Iterable[str]:
        qs = getattr(record, qs_name, None)
        if qs is not None and hasattr(qs, "values_list"):
            for name in qs.values_list("name", flat=True):
                yield str(name)

    raw: list[str] = []

    for n in names("genres"):
        plain = _normalize_hashtag_slug(n)
        if not plain:
            continue
        raw.extend((f"ds_{plain}", plain))

    for n in names("styles"):
        plain = _normalize_hashtag_slug(n)
        if not plain:
            continue
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
    availability_raw = getattr(record, "availability_status", "")
    availability = availability_raw

    if hasattr(record, "get_condition_display"):
        condition = record.get_condition_display()

    if hasattr(record, "get_availability_status_display"):
        availability = record.get_availability_status_display()

    if _is_preorder_value(str(availability_raw)) or _is_preorder_value(
        str(availability)
    ):
        condition = ""

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

    — Авторизация по-пользовательскому токену.
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
        self,
        record: Record,
        message_template: str | None = None,
        *,
        publish_at: datetime | None = None,
    ) -> int:
        """
        Публикует релиз с обложкой и, по возможности, с MP3-превью треков.
        """
        result = self.post_record_with_audio_details(
            record,
            message_template,
            publish_at=publish_at,
        )
        return result.post_id

    def post_record_with_audio_details(
        self,
        record: Record,
        message_template: str | None = None,
        *,
        publish_at: datetime | None = None,
    ) -> VKPublicationResult:
        """
        Публикует релиз в VK и возвращает детали публикации для job report.
        """
        prepared = self.prepare_record_publication(
            record,
            message_template,
            with_audio=True,
        )
        return self.publish_prepared_publication(
            record=record,
            prepared=prepared,
            publish_at=publish_at,
        )

    def prepare_record_publication(
        self,
        record: Record,
        message_template: str | None = None,
        *,
        with_audio: bool = True,
    ) -> VKPreparedPublication:
        """
        Подготавливает текст и вложения для публикации релиза в VK.

        Тяжёлые операции загрузки фото и аудио выполняются здесь один раз,
        чтобы retry публикации не переиспользовал сетевые upload-операции.
        """
        message = _render_record_message(record, message_template)
        attachment_result = self._collect_release_attachments(
            record,
            with_audio=with_audio,
        )
        return VKPreparedPublication(
            message=message,
            attachments=attachment_result.attachments,
            photo_expected=attachment_result.photo_expected,
            photo_uploaded=attachment_result.photo_uploaded,
            audio_expected_count=attachment_result.audio_expected_count,
            audio_uploaded_count=attachment_result.audio_uploaded_count,
            audio_failed_count=attachment_result.audio_failed_count,
            failed_track_titles=attachment_result.failed_track_titles,
            audio_failure_details=attachment_result.audio_failure_details,
        )

    def publish_prepared_publication(
        self,
        *,
        record: Record,
        prepared: VKPreparedPublication,
        publish_at: datetime | None = None,
    ) -> VKPublicationResult:
        """
        Публикует заранее подготовленный пост в VK без повторной загрузки вложений.
        """
        all_attachments = prepared.attachments
        record_id = getattr(record, "pk", None)
        attachments_str = ",".join(all_attachments) if all_attachments else "—"
        log_event(
            logger,
            logging.INFO,
            f"VK: публикую релиз (record_id={record_id}) с {len(all_attachments)} "
            f"вложениями: {attachments_str}",
            component=_VK_SERVICE_COMPONENT,
            event="post_start",
            record_id=record_id,
            attachments_total=len(all_attachments),
            attachments=attachments_str,
        )
        publish_date_ts = int(publish_at.timestamp()) if publish_at else None
        post_id = self._wall_post(
            message=prepared.message,
            attachments=all_attachments or None,
            publish_date_ts=publish_date_ts,
            record_id=record_id,
        )
        return VKPublicationResult(
            post_id=post_id,
            attachments=all_attachments,
            photo_expected=prepared.photo_expected,
            photo_uploaded=prepared.photo_uploaded,
            audio_expected_count=prepared.audio_expected_count,
            audio_uploaded_count=prepared.audio_uploaded_count,
            audio_failed_count=prepared.audio_failed_count,
            failed_track_titles=prepared.failed_track_titles,
            audio_failure_details=prepared.audio_failure_details,
        )

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

    # -------------------------------------------------------------------------
    # High-level internal helpers
    # -------------------------------------------------------------------------

    def _collect_release_attachments(
        self, record: Record, *, with_audio: bool
    ) -> VKAttachmentCollectionResult:
        """
        Собирает вложения релиза:
        - пытается прикрепить обложку (photo)
        - при with_audio=True пытается прикрепить превью треков (audio) с лимитом VK
        - если аудио есть, но фото нет — удаляет аудио (требование VK)
        """
        attachments: list[str] = []
        photo_expected = False
        photo_uploaded = False
        audio_expected_count = 0
        audio_uploaded_count = 0
        audio_failed_count = 0
        failed_track_titles: list[str] = []
        audio_failure_details: list[dict[str, str]] = []

        # 1) фото
        cover_path = _record_cover_path(record)
        if cover_path:
            photo_expected = True
            photo = self._upload_photo(cover_path)
            if photo:
                attachments.append(photo)
                photo_uploaded = True
            else:
                log_event(
                    logger,
                    logging.WARNING,
                    "VK: фото релиза не загружено. Аудио не будет загружаться, публикация продолжится текстом.",
                    component=_VK_SERVICE_COMPONENT,
                    event="photo_missing_audio_skipped",
                    record_id=getattr(record, "pk", None),
                    cover_path=str(cover_path),
                )
                return VKAttachmentCollectionResult(
                    attachments=attachments,
                    photo_expected=photo_expected,
                    photo_uploaded=photo_uploaded,
                    audio_expected_count=audio_expected_count,
                    audio_uploaded_count=audio_uploaded_count,
                    audio_failed_count=audio_failed_count,
                    failed_track_titles=failed_track_titles,
                    audio_failure_details=audio_failure_details,
                )
        else:
            logger.warning(
                "VK: для записи отсутствует обложка. "
                "Аудио не будут загружаться, публикация продолжится текстом.",
                extra=build_log_extra(
                    component=_VK_SERVICE_COMPONENT,
                    event="cover_missing",
                    record_id=getattr(record, "pk", None),
                ),
            )
            return VKAttachmentCollectionResult(
                attachments=attachments,
                photo_expected=photo_expected,
                photo_uploaded=photo_uploaded,
                audio_expected_count=audio_expected_count,
                audio_uploaded_count=audio_uploaded_count,
                audio_failed_count=audio_failed_count,
                failed_track_titles=failed_track_titles,
                audio_failure_details=audio_failure_details,
            )

        if not with_audio:
            return VKAttachmentCollectionResult(
                attachments=attachments,
                photo_expected=photo_expected,
                photo_uploaded=photo_uploaded,
                audio_expected_count=audio_expected_count,
                audio_uploaded_count=audio_uploaded_count,
                audio_failed_count=audio_failed_count,
                failed_track_titles=failed_track_titles,
                audio_failure_details=audio_failure_details,
            )

        # 2) аудио
        audio_attachments: list[str] = []
        missing_file_count = 0
        upload_failed_count = 0
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
            total_tracks = tracks.count()
            eligible_tracks = audio_qs.count()
            attempt_limit = max(0, limit)
            attempts = min(eligible_tracks, attempt_limit)
            skipped_due_to_limit = max(0, eligible_tracks - attempts)
            audio_expected_count = attempts
            log_event(
                logger,
                logging.DEBUG,
                "VK: аудио-кандидаты подготовлены.",
                component=_VK_SERVICE_COMPONENT,
                event="audio_candidates_prepared",
                record_id=getattr(record, "pk", None),
                tracks_total=total_tracks,
                tracks_with_audio=eligible_tracks,
                attempt_limit=attempt_limit,
                skipped_due_to_limit=skipped_due_to_limit,
            )

            for track in audio_qs[:attempt_limit]:
                preview = getattr(track, "audio_preview", None)
                p = Path(getattr(preview, "path", "")) if preview else None

                if p and p.exists():
                    att = self._upload_audio(
                        p,
                        artists,
                        getattr(track, "title", ""),
                        record_id=getattr(record, "pk", None),
                        track_id=getattr(track, "pk", None),
                    )
                    if att:
                        audio_attachments.append(att)
                    else:
                        upload_failed_count += 1
                        track_title = str(getattr(track, "title", ""))
                        failed_track_titles.append(track_title)
                        audio_failure_details.append(
                            {
                                "track_id": str(getattr(track, "pk", "")),
                                "track_title": track_title,
                                "reason": "upload_failed",
                                "message": "VK не принял загрузку аудио после повторных попыток.",
                            }
                        )
                        log_event(
                            logger,
                            logging.WARNING,
                            "VK: аудио не загружено — трек пропущен.",
                            component=_VK_SERVICE_COMPONENT,
                            event="track_audio_upload_failed",
                            record_id=getattr(record, "pk", None),
                            track_id=getattr(track, "pk", None),
                            track_title=getattr(track, "title", ""),
                            audio_path=str(p),
                        )
                else:
                    missing_file_count += 1
                    track_title = str(getattr(track, "title", ""))
                    failed_track_titles.append(track_title)
                    audio_failure_details.append(
                        {
                            "track_id": str(getattr(track, "pk", "")),
                            "track_title": track_title,
                            "reason": "file_missing",
                            "message": "Локальный mp3-файл отсутствует на диске.",
                        }
                    )
                    logger.warning(
                        "VK: у записи отсутствует mp3-файл на диске — трек пропущен.",
                        extra=build_log_extra(
                            component=_VK_SERVICE_COMPONENT,
                            event="track_audio_missing",
                            record_id=getattr(record, "pk", None),
                            track_id=getattr(track, "pk", None),
                            track_title=getattr(track, "title", ""),
                            audio_path=str(p) if p else "—",
                        ),
                    )

            log_event(
                logger,
                logging.INFO,
                (
                    "VK: аудио-итог — загружено "
                    f"{len(audio_attachments)}, пропущено {missing_file_count} "
                    f"(нет файла), ошибок загрузки {upload_failed_count}."
                ),
                component=_VK_SERVICE_COMPONENT,
                event="audio_summary",
                record_id=getattr(record, "pk", None),
                uploaded=len(audio_attachments),
                missing_file=missing_file_count,
                upload_failed=upload_failed_count,
                tracks_with_audio=eligible_tracks,
                attempt_limit=attempt_limit,
            )

        audio_uploaded_count = len(audio_attachments)
        audio_failed_count = missing_file_count + upload_failed_count
        return VKAttachmentCollectionResult(
            attachments=attachments + audio_attachments,
            photo_expected=photo_expected,
            photo_uploaded=photo_uploaded,
            audio_expected_count=audio_expected_count,
            audio_uploaded_count=audio_uploaded_count,
            audio_failed_count=audio_failed_count,
            failed_track_titles=failed_track_titles,
            audio_failure_details=audio_failure_details,
        )

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
            log_event(
                logger,
                logging.ERROR,
                "Не удалось получить upload_url для фото VK.",
                component=_VK_SERVICE_COMPONENT,
                event="photo_upload_url_failed",
                error=str(e),
            )
            return None

        upload_resp: dict[str, Any] | None = None
        last_error: requests.RequestException | None = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                with image_path.open("rb") as f:
                    resp = requests.post(
                        url,
                        files={"photo": (image_path.name, f, "image/jpeg")},
                        timeout=30,
                    )
                    resp.raise_for_status()
                upload_resp = resp.json()
                break
            except requests.RequestException as e:
                last_error = e
                logger.exception(
                    "Ошибка HTTP при загрузке фото VK.",
                    extra=build_log_extra(
                        component=_VK_SERVICE_COMPONENT,
                        event="photo_upload_http_failed",
                        error=str(e),
                        photo_attempt=attempt,
                        photo_attempts_total=max_attempts,
                        image_path=str(image_path),
                    ),
                )
                if attempt < max_attempts:
                    time.sleep(2 * attempt)

        if upload_resp is None:
            log_event(
                logger,
                logging.ERROR,
                "Не удалось загрузить фото VK после повторных попыток.",
                component=_VK_SERVICE_COMPONENT,
                event="photo_upload_retries_exhausted",
                error=str(last_error) if last_error else "unknown",
                image_path=str(image_path),
                photo_attempts_total=max_attempts,
            )
            return None

        try:
            saved = self._save_wall_photo(upload_resp)
            return f"photo{saved['owner_id']}_{saved['id']}"
        except ApiError as e:
            log_event(
                logger,
                logging.ERROR,
                "Ошибка сохранения фото VK (photos.saveWallPhoto).",
                component=_VK_SERVICE_COMPONENT,
                event="photo_save_failed",
                error=str(e),
            )
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

    def _upload_audio(
        self,
        audio_path: Path,
        artist: str,
        title: str,
        *,
        record_id: int | None = None,
        track_id: int | None = None,
    ) -> str | None:
        """
        Загружает MP3 и возвращает 'audio<owner_id>_<id>' или None, если Audio API недоступен.
        """
        try:
            url = self._get_audio_upload_url()
        except ApiError as e:
            code = getattr(e, "code", None)
            if code == 270:
                log_event(
                    logger,
                    NOTICE_LEVEL,
                    "Audio API отключён для приложения (код 270). Аудио пропущено.",
                    component=_VK_SERVICE_COMPONENT,
                    event="audio_api_disabled",
                    record_id=record_id,
                    track_id=track_id,
                    audio_path=str(audio_path),
                )
            else:
                log_event(
                    logger,
                    logging.WARNING,
                    "Не удалось получить upload_url для аудио VK.",
                    component=_VK_SERVICE_COMPONENT,
                    event="audio_upload_url_failed",
                    error=str(e),
                    record_id=record_id,
                    track_id=track_id,
                    audio_path=str(audio_path),
                )
            return None

        upload_resp: dict[str, Any] | None = None
        last_error: requests.RequestException | None = None
        backoff_schedule = (2, 5, 10)
        max_attempts = len(backoff_schedule)
        for attempt, sleep_seconds in enumerate(backoff_schedule, start=1):
            try:
                with audio_path.open("rb") as f:
                    resp = requests.post(
                        url,
                        files={"file": (audio_path.name, f, "audio/mpeg")},
                        timeout=60,
                    )
                    resp.raise_for_status()
                upload_resp = resp.json()
                break
            except requests.RequestException as e:
                last_error = e
                status_code = str(
                    getattr(getattr(e, "response", None), "status_code", "") or ""
                )
                logger.exception(
                    "Ошибка HTTP при загрузке аудио VK.",
                    extra=build_log_extra(
                        component=_VK_SERVICE_COMPONENT,
                        event="audio_upload_http_failed",
                        error=str(e),
                        http_status=status_code or "—",
                        audio_attempt=attempt,
                        audio_attempts_total=max_attempts,
                        record_id=record_id,
                        track_id=track_id,
                        audio_path=str(audio_path),
                    ),
                )
                if attempt < max_attempts:
                    time.sleep(sleep_seconds)

        if upload_resp is None:
            log_event(
                logger,
                logging.ERROR,
                "Не удалось загрузить аудио VK после повторных попыток.",
                component=_VK_SERVICE_COMPONENT,
                event="audio_upload_retries_exhausted",
                error=str(last_error) if last_error else "unknown",
                record_id=record_id,
                track_id=track_id,
                audio_path=str(audio_path),
                audio_attempts_total=max_attempts,
            )
            return None

        try:
            saved = self._save_audio(upload_resp, artist, title)
            return f"audio{saved['owner_id']}_{saved['id']}"
        except ApiError as e:
            log_event(
                logger,
                logging.WARNING,
                "Ошибка audio.save — аудио пропущено.",
                component=_VK_SERVICE_COMPONENT,
                event="audio_save_failed",
                error=str(e),
                error_code=getattr(e, "code", None),
                record_id=record_id,
                track_id=track_id,
                audio_path=str(audio_path),
            )
            return None

    def _wall_post(
        self,
        message: str,
        attachments: Sequence[str] | None = None,
        *,
        publish_date_ts: int | None = None,
        record_id: int | None = None,
    ) -> int:
        """
        Вызов VK API wall.post и получение post_id опубликованной записи.
        attachments — список attachment-строк вида "photo{owner_id}_{media_id}" и т.п.
        """
        attach_param: str | None
        if attachments:
            attach_param = ",".join(att for att in attachments if att)
        else:
            attach_param = None
        params: Dict[str, Any] = {
            "owner_id": self.owner_id,
            "message": message,
            "attachments": attach_param,
            "from_group": 1,
        }
        if publish_date_ts is not None:
            params["publish_date"] = publish_date_ts

        resp: Dict[str, Any] = self._vk.method("wall.post", params)

        post_id_raw = resp.get("post_id")
        if not isinstance(post_id_raw, int):
            log_event(
                logger,
                logging.ERROR,
                "wall.post вернул неожиданный post_id.",
                component=_VK_SERVICE_COMPONENT,
                event="post_id_invalid",
                record_id=record_id,
                post_id=post_id_raw,
                response=resp,
            )
            raise ValueError(
                f"VK: wall.post вернул некорректный post_id: {post_id_raw!r}"
            )

        log_event(
            logger,
            logging.INFO,
            "Запись опубликована в VK.",
            component=_VK_SERVICE_COMPONENT,
            event="post_published",
            record_id=record_id,
            post_id=post_id_raw,
        )
        return post_id_raw

from __future__ import annotations

from django import template
from django.urls import NoReverseMatch, reverse

from records.models import YouTubeSessionState
from records.services.audio.providers.youtube_session import YouTubeSessionService

register = template.Library()


@register.inclusion_tag(
    "admin/includes/youtube_session_banner.html",
    takes_context=True,
)
def youtube_session_banner(context: dict[str, object]) -> dict[str, object]:
    request = context.get("request")
    if request is None:
        return {"show_banner": False}

    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False) or not getattr(
        user, "is_staff", False
    ):
        return {"show_banner": False}

    state = YouTubeSessionState.get_solo()
    has_unknown_error = bool(state.status_message and state.last_error_at)
    if state.status == state.Status.HEALTHY:
        return {"show_banner": False}
    if state.status == state.Status.UNKNOWN and not has_unknown_error:
        return {"show_banner": False}

    try:
        refresh_url = reverse("admin:records_record_youtube_session_refresh")
        recover_url = reverse("admin:records_record_youtube_session_recover")
    except NoReverseMatch:
        return {"show_banner": False}

    banner_level = "warning"
    if state.status == state.Status.LOGIN_IN_PROGRESS:
        banner_level = "info"
    if state.status == state.Status.UNKNOWN:
        banner_level = "warning"

    show_login_action = state.status in {
        state.Status.AUTH_REQUIRED,
        state.Status.LOGIN_IN_PROGRESS,
    }
    show_refresh_action = True
    status_label = state.get_status_display()
    if state.status == state.Status.UNKNOWN and has_unknown_error:
        status_label = "Ошибка загрузки аудио YouTube"

    return {
        "show_banner": True,
        "state": state,
        "status_label": status_label,
        "banner_level": banner_level,
        "refresh_url": refresh_url,
        "recover_url": recover_url,
        "show_refresh_action": show_refresh_action,
        "show_login_action": show_login_action,
        "ui_url": YouTubeSessionService.resolved_ui_url(request),
        "request": request,
    }

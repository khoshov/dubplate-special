from __future__ import annotations

from django import template
from django.conf import settings
from django.urls import NoReverseMatch, reverse

from records.models import YouTubeSessionState

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
    if state.status in {state.Status.UNKNOWN, state.Status.HEALTHY}:
        return {"show_banner": False}

    try:
        refresh_url = reverse("admin:records_record_youtube_session_refresh")
        recover_url = reverse("admin:records_record_youtube_session_recover")
    except NoReverseMatch:
        return {"show_banner": False}

    banner_level = "warning"
    if state.status == state.Status.LOGIN_IN_PROGRESS:
        banner_level = "info"

    return {
        "show_banner": True,
        "state": state,
        "banner_level": banner_level,
        "refresh_url": refresh_url,
        "recover_url": recover_url,
        "ui_url": str(getattr(settings, "YOUTUBE_SESSION_UI_URL", "") or "").strip(),
        "request": request,
    }

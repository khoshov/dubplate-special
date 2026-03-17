import html

import pytest
from django.contrib import messages
from config.logging import NOTICE_LEVEL

# noinspection PyProtectedMember
from records.admin.actions import _batch_update, update_from_redeye
from records.admin import actions as actions_module
from records.models import Record


class FakeAdmin:
    def __init__(self):
        self.messages = []

    def message_user(self, request, message, level):
        self.messages.append((message, level))


class FakeUser:
    def __init__(self):
        self.username = "test-user"


class FakeRequest:
    def __init__(self):
        self.user = FakeUser()


class _RecordingRecordService:
    def __init__(self):
        self.calls = []

    def attach_audio_from_redeye(self, *, record, force=False, require_source=False):
        self.calls.append(
            {
                "record_id": record.pk,
                "force": force,
                "require_source": require_source,
            }
        )
        return 1


class _FailingRecordService:
    def attach_audio_from_redeye(self, *, record, force=False, require_source=False):
        raise ValueError(
            "Обновление из Redeye невозможно: не найден релиз с точным совпадением каталожного номера 'SP34'."
        )


@pytest.mark.django_db
def test_batch_update_empty_queryset():
    qs = Record.objects.none()
    admin = FakeAdmin()
    req = FakeRequest()
    calls = {"count": 0}

    def get_id(record: Record) -> str | None:
        return "123"

    def do_update(record: Record) -> object:
        calls["count"] += 1
        return calls

    _batch_update(
        admin_obj=admin,  # type: ignore[arg-type]
        request=req,  # type: ignore[arg-type]
        queryset=qs,
        start_log="START",
        empty_msg="EMPTY",
        ok_msg="OK",
        skip_msg="SKIP",
        skip_header="SKIP_HEADER",
        fail_msg="FAIL",
        fail_header="FAIL_HEADER",
        id_label="record_id",
        get_id=get_id,
        do_update=do_update,
    )

    assert calls["count"] == 0
    assert len(admin.messages) == 1
    msg, level = admin.messages[0]
    assert msg == "EMPTY"
    assert level == messages.WARNING


@pytest.mark.django_db
def test_update_from_redeye_action_uses_strict_attach_audio_mode():
    record = Record.objects.create(title="R1", catalog_number="SP34")
    qs = Record.objects.filter(pk=record.pk)
    admin = FakeAdmin()
    admin.record_service = _RecordingRecordService()
    req = FakeRequest()

    update_from_redeye(
        admin_obj=admin,  # type: ignore[arg-type]
        request=req,  # type: ignore[arg-type]
        queryset=qs,
    )

    assert admin.record_service.calls[0]["record_id"] == record.pk
    assert admin.record_service.calls[0]["require_source"] is True
    assert admin.record_service.calls[0]["force"] is False
    assert any("Обновлено из Redeye: 1 из 1." in msg for msg, _ in admin.messages)


@pytest.mark.django_db
def test_update_from_redeye_action_shows_error_when_exact_match_not_found(monkeypatch):
    record = Record.objects.create(title="R1", catalog_number="SP34")
    qs = Record.objects.filter(pk=record.pk)
    admin = FakeAdmin()
    admin.record_service = _FailingRecordService()
    req = FakeRequest()
    exception_calls: list[tuple] = []
    log_calls: list[tuple] = []

    monkeypatch.setattr(
        actions_module.logger,
        "exception",
        lambda *args, **kwargs: exception_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        actions_module.logger,
        "log",
        lambda *args, **kwargs: log_calls.append((args, kwargs)),
    )

    update_from_redeye(
        admin_obj=admin,  # type: ignore[arg-type]
        request=req,  # type: ignore[arg-type]
        queryset=qs,
    )

    all_messages = html.unescape("\n".join(msg for msg, _ in admin.messages))
    assert "С ошибками: 1." not in all_messages
    assert "Обновление записи с id #" in all_messages
    assert "«R1» из Redeye невозможно" in all_messages
    assert "на сайте не найден релиз с каталожным номером 'SP34'" in all_messages
    assert exception_calls == []
    assert any(
        args[0] == NOTICE_LEVEL
        and "Операция завершилась ожидаемой ошибкой." in str(args[1])
        for args, _ in log_calls
    )


@pytest.mark.django_db
def test_update_from_redeye_action_skips_none_like_catalog_number():
    Record.objects.create(title="R1", catalog_number=None)
    Record.objects.create(title="R2", catalog_number=" none ")

    qs = Record.objects.all().order_by("pk")
    admin = FakeAdmin()
    admin.record_service = _RecordingRecordService()
    req = FakeRequest()

    update_from_redeye(
        admin_obj=admin,  # type: ignore[arg-type]
        request=req,  # type: ignore[arg-type]
        queryset=qs,
    )

    assert admin.record_service.calls == []
    all_messages = "\n".join(msg for msg, _ in admin.messages)
    assert "Пропущено (нет каталожного номера): 2." in all_messages
    assert "Обновлено из Redeye" not in all_messages

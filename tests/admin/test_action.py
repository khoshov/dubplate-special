import html
import uuid

import pytest
from django.contrib import messages

from records.admin.actions import _batch_update, update_from_redeye
from records.models import Record


class FakeAdmin:
    def __init__(self):
        self.messages = []

    def message_user(self, request, message, level):
        self.messages.append((message, level))


class FakeUser:
    def __init__(self):
        self.username = "test-user"
        self.id = 321


class FakeRequest:
    def __init__(self):
        self.user = FakeUser()


class _RecordingRecordService:
    def __init__(self, *, job_id: uuid.UUID | None = None):
        self.calls = []
        self.job_id = job_id or uuid.uuid4()

    def enqueue_manual_redeye_audio_enrichment(
        self, *, record_ids, requested_by_user_id=None
    ):
        self.calls.append(
            {
                "record_ids": record_ids,
                "requested_by_user_id": requested_by_user_id,
            }
        )
        return type("Job", (), {"id": self.job_id})()


class _FailingRecordService:
    def enqueue_manual_redeye_audio_enrichment(
        self, *, record_ids, requested_by_user_id=None
    ):
        raise RuntimeError("boom")


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
def test_update_from_redeye_action_enqueues_job():
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

    all_messages = html.unescape("\n".join(str(msg) for msg, _ in admin.messages))
    assert admin.record_service.calls == [
        {
            "record_ids": [record.pk],
            "requested_by_user_id": req.user.id,
        }
    ]
    assert "Поставлена в очередь задача обновления аудио из Redeye для 1 записей." in all_messages
    assert (
        f"/admin/records/audioenrichmentjob/{admin.record_service.job_id}/change/"
        in all_messages
    )


@pytest.mark.django_db
def test_update_from_redeye_action_shows_enqueue_error():
    record = Record.objects.create(title="R1", catalog_number="SP34")
    qs = Record.objects.filter(pk=record.pk)
    admin = FakeAdmin()
    admin.record_service = _FailingRecordService()
    req = FakeRequest()

    update_from_redeye(
        admin_obj=admin,  # type: ignore[arg-type]
        request=req,  # type: ignore[arg-type]
        queryset=qs,
    )

    all_messages = html.unescape("\n".join(str(msg) for msg, _ in admin.messages))
    assert "Не удалось запустить обновление аудио из Redeye: boom" in all_messages


@pytest.mark.django_db
def test_update_from_redeye_action_enqueues_even_without_catalog_validation():
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

    assert admin.record_service.calls == [
        {
            "record_ids": list(qs.values_list("pk", flat=True)),
            "requested_by_user_id": req.user.id,
        }
    ]
    all_messages = "\n".join(str(msg) for msg, _ in admin.messages)
    assert "Поставлена в очередь задача обновления аудио из Redeye для 2 записей." in all_messages

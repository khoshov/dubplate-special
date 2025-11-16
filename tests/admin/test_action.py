import pytest
from django.contrib import messages

# noinspection PyProtectedMember
from records.admin.actions import _batch_update
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

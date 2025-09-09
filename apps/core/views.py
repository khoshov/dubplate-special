# app/views.py
from urllib.parse import unquote

from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.storage import default_storage
from django.core.signing import Signer, BadSignature
from django.http import FileResponse, Http404
from django.utils.encoding import iri_to_uri
import mimetypes

signer = Signer(salt="storage-proxy")  # соль должна совпадать с WEBDAV_PROXY_SALT

@staff_member_required
def storage_proxy(request, name: str, signature: str):
    # Django уже распаковал %XX в <path:name>, тут «чистое» относительное имя

    name = unquote(name)

    try:
        # проверяем, что "<name>:<signature>" — корректная подпись
        signer.unsign(f"{name}:{signature}")
    except BadSignature:
        raise Http404("Bad signature")

    if not default_storage.exists(name):
        raise Http404("Not found")

    f = default_storage.open(name, "rb")
    content_type, _ = mimetypes.guess_type(name)
    resp = FileResponse(f, content_type=content_type or "application/octet-stream")
    resp["Content-Disposition"] = f'inline; filename="{iri_to_uri(name.split("/")[-1])}"'
    resp["Cache-Control"] = "private, max-age=60"
    return resp

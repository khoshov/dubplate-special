# storages/webdav.py
from __future__ import annotations

import io
import posixpath
from datetime import datetime, timezone
from typing import Iterable, Tuple, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter, Retry

from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage
from django.utils.encoding import force_str

from django.core.signing import Signer
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode
from urllib.parse import urljoin


def setting(name, default=None):
    return getattr(settings, name, default)


def _url_join(base: str, *parts: str) -> str:
    """
    Корректно склеивает URL без потери слешей и с URL-эскейпингом сегментов.
    """
    base = base.rstrip("/")
    quoted = [quote(p.strip("/")) for p in parts if p and p != "/"]
    return "/".join([base, *quoted]) + ("/" if parts and parts[-1].endswith("/") else "")


class WebDavStorage(Storage):
    """
    Минимально-зависимый Storage для Django 5 с поддержкой:
      - OAuth (Яндекс.Диск) или Basic-Auth
      - таймаутов и ретраев
      - listdir через PROPFIND (глубина 1)
      - exists/size/delete/open/save/url
      - get_modified_time
      - префикса (root_path) внутри хранилища
    """

    def __init__(
        self,
        *,
        webdav_url: Optional[str] = None,
        public_url: Optional[str] = None,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        root_path: str = "",
        timeout: float = None,
        verify_ssl: Optional[bool] = None,
        max_retries: int = None,
        proxy_enabled: bool | None = None,
        proxy_url_name: str | None = None,  # имя urlpattern-а вашей вьюхи
        proxy_salt: str | None = None,  # соль для подписи
        proxy_absolute_base: str | None = None  # базовый домен, если нужен абсолютный URL
    ):
        self.webdav_url = (webdav_url or setting("WEBDAV_URL")) or ""
        self.public_url = public_url or setting("WEBDAV_PUBLIC_URL") or self.webdav_url
        self.root_path = root_path or setting("WEBDAV_ROOT_PATH", "")
        self.proxy_enabled = setting("WEBDAV_PROXY_ENABLED", False) if proxy_enabled is None else proxy_enabled
        self.proxy_url_name = setting("WEBDAV_PROXY_URL_NAME", "storage-proxy") if proxy_url_name is None else proxy_url_name
        self.proxy_salt = setting("WEBDAV_PROXY_SALT", "storage-proxy") if proxy_salt is None else proxy_salt
        self.proxy_absolute_base = setting("WEBDAV_PROXY_ABSOLUTE_BASE", None) if proxy_absolute_base is None else proxy_absolute_base
        self._signer = Signer(salt=self.proxy_salt)

        if not self.webdav_url:
            raise NotImplementedError("Please define WEBDAV_URL")

        self.timeout = (
            timeout
            if timeout is not None
            else setting("WEBDAV_TIMEOUT", 30.0)
        )
        self.verify_ssl = (
            verify_ssl
            if verify_ssl is not None
            else setting("WEBDAV_VERIFY_SSL", True)
        )
        self.max_retries = (
            max_retries
            if max_retries is not None
            else setting("WEBDAV_MAX_RETRIES", 3)
        )

        # auth
        self.token = token or setting("WEBDAV_TOKEN")  # для Яндекс.Диска: OAuth <token>
        self.username = username or setting("WEBDAV_USERNAME")
        self.password = password or setting("WEBDAV_PASSWORD")

        # requests.Session с ретраями
        self.session = requests.Session()
        retries = Retry(
            total=self.max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "PUT", "HEAD", "DELETE", "PROPFIND", "MKCOL"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # Заголовки авторизации
        self._base_headers = {}
        if self.token:
            # Яндекс.Диск WebDAV: Authorization: OAuth <token>
            self._base_headers["Authorization"] = f"OAuth {self.token}"
        elif self.username and self.password:
            self.session.auth = (self.username, self.password)

    # ---------- Вспомогательные ----------

    def _normpath(self, name: str) -> str:
        name = force_str(name).lstrip("/")
        root = (self.root_path or "").strip("/")

        if not root:
            return name

        # если имя уже начинается с префикса — не дублируем его
        if name == root or name.startswith(root + "/"):
            return name

        return f"{root}/{name}" if name else root

    def _url(self, name: str) -> str:
        return _url_join(self.webdav_url, self._normpath(name))

    def _public(self, name: str) -> str:
        return _url_join(self.public_url, self._normpath(name))

    def _req(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        headers.update(self._base_headers)
        resp = self.session.request(
            method,
            url,
            headers=headers,
            timeout=self.timeout,
            verify=self.verify_ssl,
            **kwargs,
        )
        # Считаем 2xx/3xx успешными для некоторых методов
        if not (200 <= resp.status_code < 400):
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                # Добавим контекст
                raise requests.HTTPError(
                    f"{e} [{method} {url}] body={resp.text[:500]}"
                ) from None
        return resp

    # ---------- Django Storage API ----------

    def _open(self, name, mode="rb") -> File:
        url = self._url(name)
        # stream=True для больших файлов; загрузим в память (можно сделать temp file при желании)
        resp = self._req("GET", url, stream=True)
        data = resp.content if not hasattr(resp, "raw") else resp.raw.read()
        return ContentFile(data, name=posixpath.basename(name))

    def _save(self, name, content) -> str:
        url = self._url(name)

        # Создадим директории (MKCOL) если нужно
        if setting("WEBDAV_RECURSIVE_MKCOL", True):
            self._mkcol_recursive(name)

        content_type = getattr(content, "content_type", None) or "application/octet-stream"

        # Перемотаем и отправим потоком
        if hasattr(content, "seek"):
            content.seek(0)

        data = getattr(content, "file", content)
        if isinstance(data, (io.BytesIO, io.BufferedReader)):
            body = data
        else:
            # на всякий случай читаем байты
            body = io.BytesIO(content.read())

        self._req("PUT", url, data=body, headers={"Content-Type": content_type})
        return name

    def delete(self, name) -> None:
        url = self._url(name)
        resp = self.session.request("DELETE", url, headers=self._base_headers,
                                    timeout=self.timeout, verify=self.verify_ssl)
        # Удаление должно быть идемпотентным
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()

    def exists(self, name) -> bool:
        url = self._url(name)
        resp = self.session.request("HEAD", url, headers=self._base_headers,
                                    timeout=self.timeout, verify=self.verify_ssl)
        return resp.status_code in (200, 204)

    def size(self, name) -> int:
        url = self._url(name)
        resp = self._req("HEAD", url)
        length = resp.headers.get("Content-Length") or resp.headers.get("content-length")
        if not length:
            raise IOError(f"Unable to get size for {name}")
        try:
            return int(length)
        except ValueError as e:
            raise IOError(f"Bad Content-Length for {name}: {length}") from e

    def url(self, name) -> str:
        if self.proxy_enabled:
            return self._proxy_url(name)
        return self._public(name)

    def _proxy_url(self, name: str) -> str:
        # имя файла как путь; экранируем, чтобы сегмент был безопасным
        safe_name = quote(str(name).lstrip("/"))  # оставляем слэши внутри <path:name>
        signature = self._signer.sign(str(name)).rsplit(":", 1)[1]  # только хвост подписи
        path = reverse(self.proxy_url_name, args=[safe_name, signature])

        if self.proxy_absolute_base:
            from urllib.parse import urljoin
            return urljoin(self.proxy_absolute_base.rstrip("/") + "/", path.lstrip("/"))
        return path

    def get_modified_time(self, name) -> datetime:
        """
        Django 5: должен возвращать aware datetime (UTC).
        """
        url = self._url(name)
        resp = self._req("HEAD", url)
        # RFC-1123, пример: 'Wed, 21 Oct 2015 07:28:00 GMT'
        last_mod = resp.headers.get("Last-Modified") or resp.headers.get("last-modified")
        if not last_mod:
            # В ряде WebDAV-серверов удобнее через PROPFIND забирать getlastmodified
            dt = self._propfind_modified(name)
            if dt:
                return dt
            # fallback: сейчас
            return datetime.now(timezone.utc)

        # Парс без зависимостей
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(last_mod)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    # ---------- PROPFIND / MKCOL / listdir ----------

    def listdir(self, path) -> Tuple[Iterable[str], Iterable[str]]:
        """
        Возвращает (подпапки, файлы) для относительного пути.
        Реализовано через PROPFIND Depth: 1.
        """
        rel = self._normpath(path or "")
        url = _url_join(self.webdav_url, rel if rel else "")
        # Тело PROPFIND с базовыми пропертями
        xml = """<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>"""
        headers = {
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
            **self._base_headers,
        }
        resp = self.session.request(
            "PROPFIND",
            url if url.endswith("/") else url + "/",
            data=xml.encode("utf-8"),
            headers=headers,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        if not (200 <= resp.status_code < 400):
            resp.raise_for_status()

        # Простейший XML-парсинг без внешних зависимостей
        from xml.etree import ElementTree as ET
        ns = {"d": "DAV:"}
        tree = ET.fromstring(resp.content)

        dirs, files = [], []
        # Каждый d:response — ресурс; первый обычно сам каталог — пропускаем
        for res in tree.findall("d:response", ns):
            href = res.findtext("d:href", default="", namespaces=ns)
            # фильтруем сам каталог
            if href.rstrip("/").endswith(rel.rstrip("/")):
                continue
            is_collection = res.find(".//d:resourcetype/d:collection", ns) is not None
            # displayname иногда пуст; вытащим из href
            name = res.findtext(".//d:displayname", default="", namespaces=ns)
            if not name:
                name = href.rstrip("/").split("/")[-1]
            name = force_str(name)
            (dirs if is_collection else files).append(name)
        return sorted(dirs), sorted(files)

    def _mkcol_recursive(self, name: str) -> None:
        # ВАЖНО: берём "сырые" части, без self._normpath(...)
        rel_parts = force_str(name).lstrip('/').split('/')[:-1]

        acc = ""
        for p in rel_parts:
            acc = f"{acc}/{p}" if acc else p
            url = self._url(acc) + "/"

            r = self.session.request(
                "HEAD", url, headers=self._base_headers,
                timeout=self.timeout, verify=self.verify_ssl
            )
            if r.status_code == 404:
                r2 = self.session.request(
                    "MKCOL", url, headers=self._base_headers,
                    timeout=self.timeout, verify=self.verify_ssl
                )
                if r2.status_code not in (200, 201, 405):  # 405 = уже существует
                    r2.raise_for_status()

    def _propfind_modified(self, name: str) -> Optional[datetime]:
        url = self._url(name)
        xml = """<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:">
  <D:prop><D:getlastmodified/></D:prop>
</D:propfind>"""
        headers = {"Depth": "0", "Content-Type": "application/xml; charset=utf-8", **self._base_headers}
        resp = self.session.request("PROPFIND", url, data=xml.encode("utf-8"),
                                    headers=headers, timeout=self.timeout, verify=self.verify_ssl)
        if not (200 <= resp.status_code < 400):
            return None
        from xml.etree import ElementTree as ET
        ns = {"d": "DAV:"}
        tree = ET.fromstring(resp.content)
        text = tree.findtext(".//d:getlastmodified", default="", namespaces=ns)
        if not text:
            return None
        from email.utils import parsedate_to_datetime
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

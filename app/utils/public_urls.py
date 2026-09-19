from __future__ import annotations

import ipaddress
import re
from contextvars import ContextVar, Token
from typing import Optional

from app.config import get_settings

_request_public_origin: ContextVar[Optional[str]] = ContextVar(
    "request_public_origin",
    default=None,
)

_INTERNAL_HOSTNAMES = frozenset(
    {
        "backend",
        "db",
        "0.0.0.0",
    }
)


def _hostname_from_host_header(host: str) -> str:
    raw = (host or "").strip()
    if not raw:
        return ""
    if raw.startswith("["):
        end = raw.find("]")
        if end != -1:
            return raw[1:end].lower()
    if raw.count(":") == 1:
        return raw.rsplit(":", 1)[0].lower()
    return raw.lower()


_DOCKER_BRIDGE_NET = ipaddress.ip_network("172.16.0.0/12")


def is_docker_internal_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return False
    return ip.is_loopback or ip in _DOCKER_BRIDGE_NET


def is_unusable_public_host(host: str) -> bool:
    name = _hostname_from_host_header(host)
    if not name or name in _INTERNAL_HOSTNAMES:
        return True
    return is_docker_internal_ip(name)


def public_origin_from_request(request) -> Optional[str]:
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    host = forwarded_host or (request.headers.get("host") or "").strip()
    if not host or is_unusable_public_host(host):
        return None
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    scheme = forwarded_proto or (str(getattr(request.url, "scheme", None) or "http"))
    if scheme not in {"http", "https"}:
        scheme = "http"
    return f"{scheme}://{host}".rstrip("/")


def set_request_public_origin(origin: Optional[str]) -> Token:
    value = (origin or "").strip().rstrip("/") or None
    return _request_public_origin.set(value)


def reset_request_public_origin(token: Token) -> None:
    _request_public_origin.reset(token)


def get_request_public_origin() -> Optional[str]:
    return _request_public_origin.get()


def _collapse_path_slashes(path: str) -> str:
    if not path or "//" not in path:
        return path
    return re.sub(r"/{2,}", "/", path)


def _uploads_path(stored: str) -> str:
    rel = stored.replace("\\", "/").lstrip("/")
    if rel.startswith("uploads/"):
        rel = rel[len("uploads/") :]
    return _collapse_path_slashes(f"/uploads/{rel}")


def public_file_url(stored: Optional[str]) -> Optional[str]:
    if stored is None:
        return None
    s = str(stored).strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        return s
    path = _uploads_path(s)
    origin = get_request_public_origin()
    if origin:
        return f"{origin}{path}"
    base = (get_settings().files_base_url or "").strip().rstrip("/")
    if base:
        return f"{base}{path}"
    return path

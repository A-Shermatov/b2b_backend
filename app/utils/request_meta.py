import ipaddress

from starlette.requests import Request


def _parse_ip(value: str | None):
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def _is_internal(ip) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local


def client_ip(request: Request) -> str:
    """Client IP for rate limits and audit.

    Proxy headers are trusted only when the TCP peer itself is an internal address
    (cloudflared / reverse proxy in the docker network). A request that reaches the
    API directly from the internet cannot pick its own IP via X-Forwarded-For.

    Behind a proxy: CF-Connecting-IP (set by Cloudflare, not spoofable through it),
    otherwise the right-most public address of X-Forwarded-For — the one appended by
    our proxy — and never the left-most, which the client controls.
    """
    from app.config import get_settings

    settings = get_settings()
    peer = request.client.host if request.client and request.client.host else None
    peer_ip = _parse_ip(peer)
    trust_headers = settings.trust_proxy_headers and (
        peer_ip is None or _is_internal(peer_ip)
    )
    if trust_headers:
        if settings.trust_cf_connecting_ip:
            cf_ip = _parse_ip(request.headers.get("cf-connecting-ip"))
            if cf_ip is not None:
                return str(cf_ip)
        forwarded = request.headers.get("x-forwarded-for") or ""
        hops = [_parse_ip(part) for part in forwarded.split(",")]
        hops = [hop for hop in hops if hop is not None]
        for hop in reversed(hops):
            if not _is_internal(hop):
                return str(hop)
        if hops:
            return str(hops[-1])
        real_ip = _parse_ip(request.headers.get("x-real-ip"))
        if real_ip is not None:
            return str(real_ip)
    return peer or "-"


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

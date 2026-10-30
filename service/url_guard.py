"""
URL safety checks — single source of truth for "is this a link we may scrape?".

Two independent layers live here:

1. Allowlist validation of user-supplied / scraped / post-redirect page URLs
   (rejection_reason, validate_url, is_allowed_url). Parsing is done with
   urllib.parse.urlsplit — never substring matching — so tricks such as
   https://anything/?olx.pl, https://olx.pl.evil.com/, https://evil.com/olx.pl
   or https://user@olx.pl@evil.com/ cannot pass.

2. Network-level SSRF guard for the scraping browser (request_is_safe): every
   request Chromium makes (including each redirect hop and sub-resources) is
   refused when it targets a private / loopback / link-local / otherwise
   non-global address.

Pure stdlib, no app imports: unit-testable on its own.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from typing import Optional
from urllib.parse import urlsplit

ALLOWED_HOSTS = ("olx.pl", "otomoto.pl", "otodom.pl")
MAX_URL_LEN = 2048          # filter_url column is String(2048)

_DEFAULT_PORTS = {"http": 80, "https": 443}

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")
# whitespace, C0/C1 controls, DEL and backslash: browsers and urlsplit disagree
# about how to treat these, which is exactly what URL-confusion attacks use.
_BAD_CHARS_RE = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+$"
)
# decimal / hex / dotted-short IPv4 spellings ("2130706433", "0x7f.1", "127.1")
_NUMERIC_HOST_RE = re.compile(r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*$")


# ── 1. Allowlist validation ────────────────────────────────────────────────────

def allowed_host(hostname: str) -> Optional[str]:
    """The allowlisted base domain `hostname` equals or is a subdomain of, else None."""
    for h in ALLOWED_HOSTS:
        if hostname == h or hostname.endswith("." + h):
            return h
    return None


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_NUMERIC_HOST_RE.match(host))


def rejection_reason(raw: Optional[str]) -> Optional[str]:
    """
    None when `raw` is an acceptable http(s) URL on an allowlisted host; otherwise
    a short reason code (also used as the funnel's invalid_url detail):
    empty | too_long | bad_chars | no_scheme | bad_scheme | bad_url | userinfo |
    no_host | ip_literal | bad_host | bad_port
    """
    url = (raw or "").strip()
    if not url:
        return "empty"
    if len(url) > MAX_URL_LEN:
        return "too_long"
    if _BAD_CHARS_RE.search(url):
        return "bad_chars"

    m = _SCHEME_RE.match(url)
    if not m:
        return "no_scheme"
    scheme = m.group(1).lower()
    if scheme not in _DEFAULT_PORTS:
        return "bad_scheme"

    try:
        parts = urlsplit(url)
        port = parts.port           # raises ValueError for junk / out-of-range ports
        hostname = parts.hostname
    except ValueError:
        return "bad_url"

    if parts.scheme.lower() != scheme:
        return "bad_url"
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        return "userinfo"
    if not hostname:
        return "no_host"
    if _is_ip_literal(hostname):
        return "ip_literal"
    # hostname is already lowercased by urlsplit; a trailing dot ("olx.pl.") is
    # rejected rather than normalised so the stored/scraped URL stays canonical.
    if not _HOSTNAME_RE.match(hostname) or allowed_host(hostname) is None:
        return "bad_host"
    if parts.netloc.endswith(":"):
        return "bad_port"
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        return "bad_port"
    return None


def is_allowed_url(url: Optional[str]) -> bool:
    return rejection_reason(url) is None


def validate_url(raw: Optional[str]) -> str:
    """Return the stripped URL, or raise ValueError(reason code)."""
    reason = rejection_reason(raw)
    if reason:
        raise ValueError(reason)
    return (raw or "").strip()


def site_of(url: str) -> Optional[str]:
    """'olx' | 'otomoto' | 'otodom' from the parsed hostname, None if not allowlisted."""
    try:
        host = (urlsplit(url).hostname or "")
    except ValueError:
        return None
    base = allowed_host(host)
    return base.split(".")[0] if base else None


# ── 2. Browser-side SSRF guard ────────────────────────────────────────────────

_SAFE_NON_NETWORK_SCHEMES = frozenset({"data", "blob", "about"})
_NETWORK_SCHEMES = frozenset({"http", "https", "ws", "wss"})
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".lan")

_DNS_TTL = 60.0
_DNS_CACHE_MAX = 2048
_dns_cache: dict[str, tuple[float, bool]] = {}


def _ip_is_public(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
    return ip.is_global and not ip.is_multicast


def _literal_ip(host: str):
    """Parse any IP spelling Chromium/the OS would accept; None if `host` is a name."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST_RE.match(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))   # 127.1, 0x7f.1, 2130706433
        except (OSError, ValueError):
            return ipaddress.IPv4Address("0.0.0.0")                # unparsable numeric → treat as blocked
    return None


def host_is_blocked_statically(host: str) -> bool:
    """True for IP literals in non-public ranges and obviously internal names. No DNS."""
    host = host.strip("[]").lower().rstrip(".")
    if not host:
        return True
    ip = _literal_ip(host)
    if ip is not None:
        return not _ip_is_public(ip)
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        return True
    return "." not in host           # single-label names resolve via search domains / hosts files


async def _resolves_only_to_public(host: str) -> bool:
    now = time.monotonic()
    hit = _dns_cache.get(host)
    if hit and hit[0] > now:
        return hit[1]
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, type=socket.SOCK_STREAM)
        ok = bool(infos) and all(
            _ip_is_public(ipaddress.ip_address(i[4][0].split("%")[0])) for i in infos)
    except (OSError, ValueError):
        ok = False                   # unresolvable → the browser couldn't load it either
    if len(_dns_cache) >= _DNS_CACHE_MAX:
        _dns_cache.clear()
    _dns_cache[host] = (now + _DNS_TTL, ok)
    return ok


async def request_is_safe(url: str, *, resolve_dns: bool = True) -> bool:
    """
    Should the scraping browser be allowed to issue a request to `url`?

    resolve_dns=False is for deployments where an upstream proxy does the DNS
    lookup and connection (the proxy cannot reach our private network, and a
    local lookup would not reflect where the request actually goes).
    """
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme in _SAFE_NON_NETWORK_SCHEMES:
            return True
        if scheme not in _NETWORK_SCHEMES:
            return False             # file:, ftp:, chrome:, view-source:, ...
        host = parts.hostname or ""
    except ValueError:
        return False
    if host_is_blocked_statically(host):
        return False
    if _literal_ip(host.strip("[]").lower()) is not None:
        return True                  # public IP literal, no lookup needed
    if resolve_dns:
        return await _resolves_only_to_public(host.lower().rstrip("."))
    return True

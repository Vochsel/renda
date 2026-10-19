"""SSRF-safe download of user-supplied https:// asset URLs.

Packages may reference `https://` assets, which the worker downloads from inside Modal. This refuses
anything that isn't https to a globally routable address, connects to the address it validated (so a
DNS answer can't change between check and connect), follows redirects itself (re-validating each hop),
and caps size and total time. Presigned R2 URLs minted by our own server don't go through here.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.parse
from pathlib import Path

MAX_BYTES = 5 * 1024**3  # same ceiling as POST /api/v1/uploads
MAX_REDIRECTS = 5


class UnsafeUrlError(ValueError):
    pass


def is_public_address(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return is_public_address(str(addr.ipv4_mapped))
        if addr.sixtofour:
            return is_public_address(str(addr.sixtofour))
        if addr.teredo:
            return False
        if addr in ipaddress.ip_network("64:ff9b::/96"):
            return is_public_address(str(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)))
    return addr.is_global and not addr.is_multicast


def resolve_public(host: str, port: int) -> str:
    """Resolve host and return one address, refusing if ANY answer is non-public."""
    try:
        ipaddress.ip_address(host)
        if not is_public_address(host):
            raise UnsafeUrlError(f"{host} is not a public address")
        return host
    except ValueError as e:
        if isinstance(e, UnsafeUrlError):
            raise
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeUrlError(f"can't resolve {host}: {e}") from e
    addrs = [i[4][0] for i in infos]
    bad = next((a for a in addrs if not is_public_address(a)), None)
    if bad or not addrs:
        raise UnsafeUrlError(f"{host} resolves to a non-public address ({bad})")
    return addrs[0]


def check_url(url: str) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https":
        raise UnsafeUrlError(f"only https:// asset URLs are allowed, got {u.scheme or 'none'}:")
    if u.username or u.password:
        raise UnsafeUrlError("credentials in URLs are not allowed")
    if not u.hostname:
        raise UnsafeUrlError("URL has no host")
    return u


def safe_download(url: str, dest: Path, *, max_bytes: int = MAX_BYTES, total_timeout: float = 1800.0,
                  connect_timeout: float = 10.0, read_timeout: float = 60.0) -> None:
    import urllib3

    deadline = time.monotonic() + total_timeout
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        u = check_url(current)
        host, port = u.hostname, u.port or 443
        ip = resolve_public(host, port)
        # Connect to the validated IP; TLS (SNI + certificate) still checked against the real host name.
        pool = urllib3.HTTPSConnectionPool(
            ip, port, server_hostname=host, assert_hostname=host,
            timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout), retries=False,
        )
        path = urllib.parse.urlunsplit(("", "", u.path or "/", u.query, ""))
        r = pool.urlopen("GET", path, headers={"Host": host, "User-Agent": "renda-worker/1"},
                         redirect=False, preload_content=False)
        try:
            if 300 <= r.status < 400:
                loc = r.headers.get("Location")
                if not loc:
                    raise ValueError(f"HTTP {r.status} without a Location header")
                current = urllib.parse.urljoin(current, loc)
                continue
            if not 200 <= r.status < 300:
                raise ValueError(f"HTTP {r.status} fetching {host}")
            length = int(r.headers.get("Content-Length") or 0)
            if length > max_bytes:
                raise ValueError(f"asset is {length} bytes (limit {max_bytes})")
            size = 0
            with open(dest, "wb") as f:
                for block in r.stream(1 << 20):
                    size += len(block)
                    if size > max_bytes:
                        raise ValueError(f"asset exceeds {max_bytes} bytes")
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"download exceeded {total_timeout:g}s")
                    f.write(block)
            return
        finally:
            r.release_conn()
            pool.close()
    raise ValueError("too many redirects")

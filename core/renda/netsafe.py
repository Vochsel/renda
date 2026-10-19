"""SSRF-safe https GET for loading package layers (port of spec/src/node.ts).

The hostname is resolved here and every address must be globally routable; the socket then connects
to that validated address (TLS still verifies the hostname), so a DNS answer can't change between
check and connect. Redirects are followed manually: https only, re-validated, at most a few hops.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

from .layers import LIMITS


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
        if addr in ipaddress.ip_network("64:ff9b::/96"):
            return is_public_address(str(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)))
    return addr.is_global and not addr.is_multicast


def _resolve_public(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeUrlError(f"can't resolve {host}: {e}") from e
    addrs = [i[4][0] for i in infos]
    bad = next((a for a in addrs if not is_public_address(a)), None)
    if bad:
        raise UnsafeUrlError(f"{host} resolves to a non-public address ({bad})")
    return addrs[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a pre-validated IP while doing TLS/SNI/Host for the real hostname."""

    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def safe_fetch_text(url: str, *, timeout: float = 10.0, max_bytes: int = LIMITS["max_bytes"], max_redirects: int = 3,
                    total_timeout: float = 30.0) -> str:
    # `timeout` bounds each socket operation; `total_timeout` bounds the whole fetch, redirects included,
    # so a server dripping one byte at a time can't hold the request open.
    deadline = time.monotonic() + total_timeout

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"fetch exceeded {total_timeout:g}s")
        return min(timeout, left)

    current = url
    for _ in range(max_redirects + 1):
        u = urllib.parse.urlsplit(current)
        if u.scheme != "https":
            raise UnsafeUrlError(f"only https:// URLs are allowed, got {u.scheme}:")
        if u.username or u.password:
            raise UnsafeUrlError("credentials in URLs are not allowed")
        host = u.hostname or ""
        port = u.port or 443
        try:
            ipaddress.ip_address(host)
            literal = True
        except ValueError:
            literal = False
        if literal:
            if not is_public_address(host):
                raise UnsafeUrlError(f"{host} is not a public address")
            ip = host
        else:
            ip = _resolve_public(host, port)
        conn = _PinnedHTTPSConnection(host, ip, port, remaining())
        try:
            path = urllib.parse.urlunsplit(("", "", u.path or "/", u.query, ""))
            conn.request("GET", path, headers={"User-Agent": "renda-layers/1", "Accept": "application/json"})
            r = conn.getresponse()
            if 300 <= r.status < 400:
                loc = r.getheader("Location")
                if not loc:
                    raise ValueError(f"HTTP {r.status} without a Location header")
                current = urllib.parse.urljoin(current, loc)
                continue
            if not 200 <= r.status < 300:
                raise ValueError(f"HTTP {r.status}")
            length = int(r.getheader("Content-Length") or 0)
            if length > max_bytes:
                raise ValueError(f"response is {length} bytes (limit {max_bytes})")
            chunks: list[bytes] = []
            size = 0
            while True:
                left = remaining()  # raises once the whole-fetch budget is spent
                if conn.sock is not None:
                    conn.sock.settimeout(left)
                chunk = r.read1(min(65536, max_bytes + 1 - size))  # read1: return what has arrived
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"response exceeds {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8")
        finally:
            conn.close()
    raise ValueError("too many redirects")

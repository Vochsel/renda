import socket

import pytest

from renda_worker import netsafe
from renda_worker.assets import AssetCache
from renda_worker.netsafe import UnsafeUrlError, check_url, is_public_address, resolve_public, safe_download


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "2002:7f00:1::", "2001:0:4136:e378::1",
    "224.0.0.1", "not-an-ip",
])
def test_non_public_addresses_refused(ip):
    assert not is_public_address(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_allowed(ip):
    assert is_public_address(ip)


@pytest.mark.parametrize("url", ["http://example.com/a.glb", "file:///etc/passwd", "https://user:pw@example.com/a", "gopher://x"])
def test_only_plain_https_urls(url):
    with pytest.raises(UnsafeUrlError):
        check_url(url)


def test_resolve_refuses_if_any_answer_is_private(monkeypatch):
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
               (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answers)
    with pytest.raises(UnsafeUrlError):
        resolve_public("rebind.example", 443)


def test_literal_private_ip_refused_before_connecting(monkeypatch, tmp_path):
    def no_network(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(socket, "create_connection", no_network)
    with pytest.raises(UnsafeUrlError):
        safe_download("https://169.254.169.254/latest/meta-data", tmp_path / "x")


def test_redirect_to_private_address_refused(monkeypatch, tmp_path):
    class Resp:
        def __init__(self, status, headers):
            self.status, self.headers = status, headers

        def release_conn(self):
            pass

    calls = []

    class Pool:
        def __init__(self, ip, port, **kw):
            calls.append(ip)

        def urlopen(self, *a, **k):
            return Resp(302, {"Location": "https://internal.example/secret"})

        def close(self):
            pass

    import urllib3

    monkeypatch.setattr(urllib3, "HTTPSConnectionPool", Pool)

    def resolve(host, port):
        if host == "internal.example":
            raise UnsafeUrlError("internal.example resolves to a non-public address (10.0.0.9)")
        return "93.184.216.34"

    monkeypatch.setattr(netsafe, "resolve_public", resolve)
    with pytest.raises(UnsafeUrlError):
        safe_download("https://cdn.example/model.glb", tmp_path / "x")
    assert calls == ["93.184.216.34"]


def test_asset_cache_uses_safe_fetcher_for_user_urls(tmp_path):
    seen = {}
    cache = AssetCache(tmp_path, {}, download=lambda u, d: (_ for _ in ()).throw(AssertionError("trusted path")),
                       download_untrusted=lambda u, d: (seen.setdefault("url", u), d.write_bytes(b"glTF")))
    path = cache.fetch("https://cdn.example/model.glb")
    assert seen["url"] == "https://cdn.example/model.glb" and path.read_bytes() == b"glTF"

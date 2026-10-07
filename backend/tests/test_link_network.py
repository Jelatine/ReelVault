import asyncio
import socket

import httpx
import pytest

from reelvault.link_network import DownloadProxy, addresses, parse_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/video",
        "http://user:pass@example.com/video",
        "http://example.com/\r\nx: y",
        "http://example.com\\@127.0.0.1/",
        "https://[::1%25eth0]/",
    ],
)
def test_invalid_download_urls(url):
    with pytest.raises(ValueError):
        parse_url(url)


def test_private_addresses_are_rejected_before_connecting():
    async def check():
        for host in ("127.0.0.1", "::1", "169.254.169.254", "10.0.0.1", "192.0.2.1"):
            with pytest.raises(ValueError):
                await addresses(host, 80)
        assert await addresses("127.0.0.1", 80, allow_private=True) == ["127.0.0.1"]

    asyncio.run(check())


def test_dns_results_are_pinned_and_mixed_private_answers_rejected(monkeypatch):
    async def check():
        resolutions = []
        connections = []

        async def resolve(host, port, **kwargs):
            resolutions.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        async def connect(host, port):
            connections.append((host, port))
            return None, None

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        monkeypatch.setattr(asyncio, "open_connection", connect)
        await DownloadProxy(1024)._connect("example.com", 443)
        assert resolutions == ["example.com"]
        assert connections == [("93.184.216.34", 443)]

        async def mixed(host, port, **kwargs):
            return await resolve(host, port) + [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
            ]

        monkeypatch.setattr(loop, "getaddrinfo", mixed)
        with pytest.raises(ValueError):
            await DownloadProxy(1024)._connect("example.com", 443)
        assert len(connections) == 1

    asyncio.run(check())


def test_real_proxy_download_redirect_gate_and_transfer_limit():
    async def check():
        paths = []

        async def source(reader, writer):
            request = await reader.readuntil(b"\r\n\r\n")
            path = request.split(b" ")[1]
            paths.append(path)
            if path == b"/redirect":
                writer.write(
                    b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/private\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
            else:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\nConnection: close\r\n\r\n"
                    + b"v" * 4096
                )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(source, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with (
                DownloadProxy(10000, allow_private=True) as proxy,
                httpx.AsyncClient(proxy=proxy.url, trust_env=False) as client,
            ):
                response = await client.get(f"http://127.0.0.1:{port}/video")
                assert response.content == b"v" * 4096
            async with DownloadProxy(10000) as proxy:
                original = proxy._connect

                async def public(host, target_port):
                    if host == "public.invalid":
                        return await asyncio.open_connection("127.0.0.1", port)
                    return await original(host, target_port)

                proxy._connect = public
                async with httpx.AsyncClient(
                    proxy=proxy.url, trust_env=False, follow_redirects=True
                ) as client:
                    response = await client.get("http://public.invalid/redirect")
                    assert response.status_code == 403
                    assert "内网" in proxy.reason
            async with (
                DownloadProxy(1024, allow_private=True) as proxy,
                httpx.AsyncClient(proxy=proxy.url, trust_env=False) as client,
            ):
                with pytest.raises(httpx.HTTPError):
                    await client.get(f"http://127.0.0.1:{port}/large")
                assert proxy.reason == "下载流量超过限制"
            assert b"/private" not in paths
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(check())


def test_connect_tunnel_is_authenticated_and_closes_with_the_job():
    async def check():
        received = []

        async def echo(reader, writer):
            received.append(await reader.readexactly(6))
            writer.write(b"opaque-response")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        destination = server.sockets[0].getsockname()[1]
        try:
            async with DownloadProxy(1024, allow_private=True) as proxy:
                port = proxy.server.sockets[0].getsockname()[1]
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(f"CONNECT 127.0.0.1:{destination} HTTP/1.1\r\n\r\n".encode())
                await writer.drain()
                assert b"407" in await reader.read()
                writer.close()
                await writer.wait_closed()
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(
                    (
                        f"CONNECT 127.0.0.1:{destination} HTTP/1.1\r\n"
                        f"Proxy-Authorization: Basic {proxy._auth}\r\n\r\n"
                    ).encode()
                )
                await writer.drain()
                assert b"200" in await reader.readuntil(b"\r\n\r\n")
                writer.write(b"opaque")
                await writer.drain()
                assert await reader.read() == b"opaque-response"
                writer.close()
                await writer.wait_closed()
                assert received == [b"opaque"]
            assert not proxy._tasks and not proxy._writers
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(check())

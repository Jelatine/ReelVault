"""A job-scoped HTTP proxy that pins connections to validated destination IPs."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import ipaddress
import secrets
import socket
from urllib.parse import urlsplit


def parse_url(url: str) -> tuple[str, int]:
    if any(ord(char) <= 32 for char in url) or "\\" in url:
        raise ValueError("链接必须是有效的 HTTP 或 HTTPS 地址")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("链接必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("链接不能包含用户名或密码")
    host = parsed.hostname.encode("idna").decode("ascii")
    if "%" in host or len(host) > 253:
        raise ValueError("链接主机名无效")
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("链接端口无效")
    return host, port


async def addresses(host: str, port: int, *, allow_private: bool = False) -> list[str]:
    try:
        records = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 10
        )
    except (OSError, TimeoutError) as error:
        raise ValueError("无法解析下载源地址") from error
    result = list(dict.fromkeys(str(record[4][0]) for record in records))
    if (
        not result
        or any(not ipaddress.ip_address(address).is_global for address in result)
        and not allow_private
    ):
        raise ValueError("下载源不能访问本机、内网或保留地址")
    return result


class DownloadProxy:
    def __init__(self, max_bytes: int, *, allow_private: bool = False) -> None:
        self.allow_private = allow_private
        self.max_bytes = max_bytes
        self.received = 0
        self.reason: str | None = None
        self._secret = secrets.token_urlsafe(24)
        self._auth = base64.b64encode(f"rv:{self._secret}".encode()).decode()
        self._tasks: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self.server: asyncio.Server | None = None
        self.url = ""

    async def __aenter__(self) -> DownloadProxy:
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=65536)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://rv:{self._secret}@127.0.0.1:{port}"
        return self

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Track accepted connections before their coroutine first runs, including shutdown races.
        task = asyncio.create_task(self._client(reader, writer))
        self._tasks.add(task)
        self._writers.add(writer)
        task.add_done_callback(self._tasks.discard)

    async def __aexit__(self, *args: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self._writers):
            writer.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _connect(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        candidates = await addresses(host, port, allow_private=self.allow_private)
        # Connect to the checked IP, never resolve the hostname a second time.
        for address in candidates:
            try:
                return await asyncio.wait_for(asyncio.open_connection(address, port), 15)
            except (OSError, TimeoutError):
                continue
        raise ValueError("无法连接下载源")

    async def _pipe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, incoming: bool = False
    ) -> None:
        while chunk := await reader.read(65536):
            if incoming:
                self.received += len(chunk)
                if self.received > self.max_bytes:
                    self.reason = "下载流量超过限制"
                    raise ValueError(self.reason)
            writer.write(chunk)
            await writer.drain()
        with contextlib.suppress(OSError, RuntimeError):
            writer.write_eof()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        self._writers.add(writer)
        upstream: asyncio.StreamWriter | None = None
        response_started = False
        pipes: list[asyncio.Task] = []
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            lines = raw.decode("latin-1").split("\r\n")
            method, target, version = lines[0].split(" ")
            headers = [line.split(":", 1) for line in lines[1:] if line]
            credentials = [v.strip() for k, v in headers if k.lower() == "proxy-authorization"]
            if len(credentials) != 1 or not hmac.compare_digest(
                credentials[0], "Basic " + self._auth
            ):
                writer.write(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n"
                )
                await writer.drain()
                return
            if version not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError("下载请求协议不受支持")
            if method == "CONNECT":
                parsed = urlsplit("//" + target)
                if not parsed.hostname or parsed.path or parsed.username or parsed.password:
                    raise ValueError("下载请求地址无效")
                host, port = parse_url("https://" + target)
            else:
                if method not in ("GET", "HEAD", "POST"):
                    raise ValueError("下载请求方法不受支持")
                host, port = parse_url(target)
                parsed = urlsplit(target)
                if parsed.scheme != "http":
                    raise ValueError("HTTPS 下载必须使用加密连接")
            remote, upstream = await self._connect(host, port)
            self._writers.add(upstream)
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                response_started = True
            else:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                authority = f"[{host}]" if ":" in host else host
                if port != 80:
                    authority += f":{port}"
                output = [f"{method} {path} {version}", "Host: " + authority, "Connection: close"]
                output += [
                    k + ":" + v
                    for k, v in headers
                    if k.lower()
                    not in ("proxy-authorization", "proxy-connection", "connection", "host")
                ]
                upstream.write(("\r\n".join(output) + "\r\n\r\n").encode("latin-1"))
                await upstream.drain()
                response_started = True
            pipes = [
                asyncio.create_task(self._pipe(reader, upstream)),
                asyncio.create_task(self._pipe(remote, writer, incoming=True)),
            ]
            done, _ = await asyncio.wait(pipes, return_when=asyncio.FIRST_COMPLETED)
            # A body-less HTTP request can finish sending before its response arrives.
            if pipes[0] in done and not pipes[0].exception():
                await pipes[1]
            for finished in done:
                finished.result()
        except (
            OSError,
            ValueError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as error:
            self.reason = str(error) if isinstance(error, ValueError) else "下载连接失败"
            if not response_started:
                with contextlib.suppress(OSError):
                    writer.write(
                        b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                    await writer.drain()
        finally:
            for pipe in pipes:
                pipe.cancel()
            await asyncio.gather(*pipes, return_exceptions=True)
            for connection in (upstream, writer):
                if connection:
                    connection.close()
                    self._writers.discard(connection)
                    with contextlib.suppress(OSError):
                        await connection.wait_closed()
            self._tasks.discard(task)

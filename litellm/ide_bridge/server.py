"""The MITM server itself.

Each spoofed hostname lands on the same listener. TLS terminates here with a
per-SNI leaf, the HTTP request is parsed by hand, routed (gateway or
passthrough) through a shared httpx client, and the upstream response is
re-emitted with our own chunked framing. The outbound client ignores proxy
environment variables so the bridge can never loop back into itself.
"""

import asyncio
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, TypeAlias

import httpx

from litellm.ide_bridge.capture import CaptureSink
from litellm.ide_bridge.certs import BridgeCertificates
from litellm.ide_bridge.config import BridgeConfig
from litellm.ide_bridge.forward import (
    HOP_BY_HOP_RESPONSE_HEADERS,
    UpstreamTarget,
    build_outbound,
)

MAX_HEAD_BYTES: Final[int] = 65536
MAX_BODY_BYTES: Final[int] = 64 * 1024 * 1024
BODY_PREVIEW_BYTES: Final[int] = 2048
UPSTREAM_TIMEOUT: Final = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
TLS_HANDSHAKE_TIMEOUT_SECONDS: Final[float] = 10.0
NO_EXTRA_HEADERS: Final[Mapping[str, str]] = MappingProxyType({})

DecideUpstream: TypeAlias = Callable[[str, str, int, BridgeConfig], UpstreamTarget]


@dataclass(frozen=True, slots=True)
class RequestHead:
    method: str
    path: str
    http_version: str
    headers: tuple[tuple[str, str], ...]

    def header(self, name: str) -> str | None:
        wanted: Final = name.lower()
        for header_name, value in self.headers:
            if header_name == wanted:
                return value
        return None

    @property
    def content_length(self) -> int | None:
        raw: Final = self.header("content-length")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @property
    def is_chunked(self) -> bool:
        transfer_encoding: Final = self.header("transfer-encoding")
        return transfer_encoding is not None and "chunked" in transfer_encoding.lower()


def normalize_path(raw_path: str) -> str:
    """Absolute-form request lines (explicit proxies) become origin-form."""
    if not raw_path.startswith(("http://", "https://")):
        return raw_path
    after_scheme: Final = raw_path.split("://", 1)[1]
    if "/" not in after_scheme:
        return "/"
    return "/" + after_scheme.split("/", 1)[1]


def parse_request_head(raw: bytes) -> RequestHead | None:
    lines: Final = raw.split(b"\r\n")
    request_line: Final = lines[0].decode("latin-1")
    parts: Final = request_line.split(" ")
    if len(parts) != 3 or not parts[0]:
        return None
    headers: Final[list[tuple[str, str]]] = []  # mutable-ok: header accumulator
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(b":")
        if not separator:
            return None
        headers.append((name.strip().decode("latin-1").lower(), value.strip().decode("latin-1")))
    return RequestHead(
        method=parts[0],
        path=normalize_path(parts[1]),
        http_version=parts[2],
        headers=tuple(headers),
    )


def request_host_port(head: RequestHead) -> tuple[str, int]:
    host_header: Final = head.header("host")
    if host_header is None:
        return ("", 443)
    host, _, port_text = host_header.partition(":")
    if port_text.isdigit():
        return (host.lower(), int(port_text))
    return (host.lower(), 443)


def wants_keep_alive(head: RequestHead) -> bool:
    connection: Final = head.header("connection")
    if connection is None:
        return head.http_version == "HTTP/1.1"
    return "close" not in connection.lower()


class BridgeServer:
    def __init__(
        self,
        *,
        config: BridgeConfig,
        certificates: BridgeCertificates,
        capture_sink: CaptureSink,
        decide_upstream: DecideUpstream,
        client_factory: Callable[[], httpx.AsyncClient],
    ) -> None:
        self._config = config
        self._certificates = certificates
        self._capture_sink = capture_sink
        self._decide_upstream = decide_upstream
        self._client_factory = client_factory
        self._upstream_client: httpx.AsyncClient | None = None

    @staticmethod
    def default_client() -> httpx.AsyncClient:
        """trust_env=False keeps system proxies (and their loops) out of the way."""
        return httpx.AsyncClient(trust_env=False, timeout=UPSTREAM_TIMEOUT)

    async def serve_forever(self) -> None:
        default_host: Final = self._config.spoof_hosts[0]
        context: Final = self._certificates.base_server_context(default_host)
        self._upstream_client = self._client_factory()
        server: Final = await asyncio.start_server(
            self._handle_client,
            host=self._config.listen_host,
            port=self._config.listen_port,
            ssl=context,
            ssl_handshake_timeout=TLS_HANDSHAKE_TIMEOUT_SECONDS,
            limit=MAX_HEAD_BYTES,
        )
        try:
            async with server:
                await server.serve_forever()
        finally:
            await self._upstream_client.aclose()
            self._upstream_client = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head_raw = await reader.readuntil(b"\r\n\r\n")
                head = parse_request_head(head_raw[:-4])
                if head is None:
                    break
                body_result = await self._read_request_body(reader, head)
                if isinstance(body_result, int):
                    await self._write_simple_response(writer, body_result, b'{"error": "request body too large"}')
                    break
                keep_alive = wants_keep_alive(head)
                await self._proxy_one(writer, head, body_result, keep_alive)
                if not keep_alive:
                    break
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            ssl.SSLError,
            OSError,
        ):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ssl.SSLError, OSError):
                pass

    async def _read_request_body(self, reader: asyncio.StreamReader, head: RequestHead) -> bytes | int:
        content_length: Final = head.content_length
        if content_length is not None:
            if content_length > MAX_BODY_BYTES:
                return 413
            if content_length == 0:
                return b""
            return await reader.readexactly(content_length)
        if head.is_chunked:
            return await self._read_chunked_body(reader)
        return b""

    async def _read_chunked_body(self, reader: asyncio.StreamReader) -> bytes | int:
        chunks: Final[list[bytes]] = []  # mutable-ok: chunked body accumulator
        while True:
            size_line = await reader.readuntil(b"\r\n")
            size_text = size_line.split(b";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError:
                return 400
            if size > MAX_BODY_BYTES:
                return 413
            if size == 0:
                while True:
                    trailer_line = await reader.readuntil(b"\r\n")
                    if trailer_line == b"\r\n":
                        return b"".join(chunks)
            if sum(map(len, chunks)) + size > MAX_BODY_BYTES:
                return 413
            chunks.append((await reader.readexactly(size + 2))[:-2])

    async def _proxy_one(
        self,
        writer: asyncio.StreamWriter,
        head: RequestHead,
        body: bytes,
        keep_alive: bool,
    ) -> None:
        host, port = request_host_port(head)
        target: Final = self._decide_upstream(host, head.path, port, self._config)
        request_headers: Final[Mapping[str, str]] = dict(head.headers)
        started: Final = time.monotonic()
        outbound: Final = build_outbound(
            target,
            head.path,
            request_headers,
            self._config.gateway_api_key or "",
        )
        if outbound is None:
            await self._write_simple_response(writer, 502, b'{"error": "no upstream target"}')
            return
        status, response_preview = await self._relay(
            writer, head.method, outbound.url, outbound.headers, body, keep_alive
        )
        duration_ms: Final = (time.monotonic() - started) * 1000
        self._capture_sink.record(
            method=head.method,
            host=host,
            path=head.path,
            upstream=outbound.upstream_label,
            status_code=status,
            duration_ms=duration_ms,
            request_headers=request_headers,
            request_body=body,
            response_body=response_preview,
        )

    async def _relay(
        self,
        writer: asyncio.StreamWriter,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        keep_alive: bool,
    ) -> tuple[int, bytes]:
        owns_client: Final = self._upstream_client is None
        client: Final = self._upstream_client if self._upstream_client is not None else self._client_factory()
        try:
            async with client.stream(method, url, headers=headers, content=body) as response:
                await self._write_response_head(writer, response, keep_alive)
                response_preview: Final = await self._stream_response_body(writer, response)
                return response.status_code, response_preview
        except (httpx.HTTPError, OSError) as upstream_error:
            await self._write_simple_response(
                writer,
                502,
                b'{"error": "upstream unreachable"}',
                extra={"x-litellm-bridge-error": str(upstream_error)[:200]},
            )
            return 502, b""
        finally:
            if owns_client:
                await client.aclose()

    async def _write_response_head(
        self, writer: asyncio.StreamWriter, response: httpx.Response, keep_alive: bool
    ) -> None:
        status_line: Final = f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n".encode("latin-1")
        header_lines: Final = "".join(
            f"{name}: {value}\r\n"
            for name, value in response.headers.items()
            if name.lower() not in HOP_BY_HOP_RESPONSE_HEADERS
        )
        connection_state: Final = "keep-alive" if keep_alive else "close"
        framing: Final = f"transfer-encoding: chunked\r\nconnection: {connection_state}\r\n\r\n"
        writer.write(status_line + header_lines.encode("latin-1") + framing.encode("latin-1"))

    async def _stream_response_body(self, writer: asyncio.StreamWriter, response: httpx.Response) -> bytes:
        preview: Final[bytearray] = bytearray()
        async for chunk in response.aiter_bytes():
            if len(preview) < BODY_PREVIEW_BYTES:
                preview.extend(chunk[: BODY_PREVIEW_BYTES - len(preview)])
            writer.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
            await writer.drain()
        writer.write(b"0\r\n\r\n")
        await writer.drain()
        return bytes(preview)

    async def _write_simple_response(
        self,
        writer: asyncio.StreamWriter,
        status_code: int,
        body: bytes,
        extra: Mapping[str, str] = NO_EXTRA_HEADERS,
    ) -> None:
        reason: Final = "Bad Gateway" if status_code == 502 else "Bad Request"
        header_lines: Final = (
            f"HTTP/1.1 {status_code} {reason}\r\n"
            "content-type: application/json\r\n"
            + "".join(f"{name}: {value}\r\n" for name, value in extra.items())
            + f"content-length: {len(body)}\r\n"
            "connection: close\r\n"
            "\r\n"
        )
        writer.write(header_lines.encode("latin-1") + body)
        await writer.drain()

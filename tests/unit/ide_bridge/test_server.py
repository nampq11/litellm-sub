import asyncio
import datetime
from contextlib import asynccontextmanager
from typing import AsyncIterator
from pathlib import Path
from typing import Callable, Final

import httpx
import pytest

from litellm.ide_bridge.capture import CaptureSink, read_captures
from litellm.ide_bridge.certs import BridgeCertificates, generate_ca, load_ca_key
from litellm.ide_bridge.config import BridgeConfig
from litellm.ide_bridge.forward import decide_upstream
from litellm.ide_bridge.server import (
    BridgeServer,
    parse_request_head,
    request_host_port,
    wants_keep_alive,
)

NOW: Final = datetime.datetime(2025, 6, 1, tzinfo=datetime.UTC)
GATEWAY_BODY: Final = {"id": "msg_1", "content": [{"type": "text", "text": "hi"}]}
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> BridgeCertificates:
    ca = generate_ca(NOW)
    return BridgeCertificates(ca.cert_pem, load_ca_key(ca.key_pem), tmp_path_factory.mktemp("leaves"))


def _config(tmp_path: Path) -> BridgeConfig:
    return BridgeConfig.model_validate(
        {
            "data_dir": tmp_path,
            "gateway_base_url": "http://gateway.test",
            "gateway_api_key": "sk-gateway-key",
            "route_hosts": ("api.anthropic.com",),
            "install_trust": False,
        }
    )


def _bridge(tmp_path: Path, certificates: BridgeCertificates, handler: Handler) -> BridgeServer:
    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return BridgeServer(
        config=_config(tmp_path),
        certificates=certificates,
        capture_sink=CaptureSink(tmp_path / "capture.jsonl"),
        decide_upstream=decide_upstream,
        client_factory=client_factory,
    )


@asynccontextmanager
async def _serving(bridge: BridgeServer):
    server = await asyncio.start_server(bridge._handle_client, host="127.0.0.1", port=0)
    try:
        yield server
    finally:
        server.close()
        await server.wait_closed()


def _port(server: asyncio.AbstractServer) -> int:
    sockets = server.sockets
    assert sockets is not None
    return sockets[0].getsockname()[1]


class TestParseRequestHead:
    def test_method_path_and_headers_preserve_order(self):
        head = parse_request_head(
            b"POST /v1/messages?beta=true HTTP/1.1\r\nHost: api.anthropic.com\r\nContent-Type: application/json\r\n\r\n"
        )

        assert head is not None
        assert head.method == "POST"
        assert head.path == "/v1/messages?beta=true"
        assert head.http_version == "HTTP/1.1"
        assert head.headers == (
            ("host", "api.anthropic.com"),
            ("content-type", "application/json"),
        )

    def test_absolute_form_request_line_becomes_origin_form(self):
        head = parse_request_head(
            b"GET https://api.anthropic.com/v1/models HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n"
        )

        assert head is not None
        assert head.path == "/v1/models"

    def test_malformed_request_line_returns_none(self):
        assert parse_request_head(b"garbage\r\n\r\n") is None

    def test_header_without_colon_returns_none(self):
        assert parse_request_head(b"GET / HTTP/1.1\r\nbroken header\r\n\r\n") is None

    def test_content_length_and_chunked_flags(self):
        with_length = parse_request_head(b"POST / HTTP/1.1\r\nContent-Length: 42\r\n\r\n")
        with_chunked = parse_request_head(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
        without = parse_request_head(b"POST / HTTP/1.1\r\n\r\n")
        bad = parse_request_head(b"POST / HTTP/1.1\r\nContent-Length: nope\r\n\r\n")

        assert with_length is not None and with_length.content_length == 42
        assert with_chunked is not None and with_chunked.is_chunked
        assert without is not None and without.content_length is None
        assert bad is not None and bad.content_length is None

    def test_header_lookup_is_case_insensitive(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\nX-Custom-Thing: yes\r\n\r\n")

        assert head is not None
        assert head.header("x-custom-thing") == "yes"


class TestHostPort:
    def test_host_with_port(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\nHost: api.anthropic.com:8443\r\n\r\n")

        assert head is not None
        assert request_host_port(head) == ("api.anthropic.com", 8443)

    def test_host_without_port_defaults_to_443(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\nHost: Api.Anthropic.com\r\n\r\n")

        assert head is not None
        assert request_host_port(head) == ("api.anthropic.com", 443)

    def test_missing_host_header_yields_empty_host(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\n\r\n")

        assert head is not None
        assert request_host_port(head) == ("", 443)


class TestWantsKeepAlive:
    def test_http_11_defaults_to_keep_alive(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\n\r\n")

        assert head is not None and wants_keep_alive(head)

    def test_http_10_defaults_to_close(self):
        head = parse_request_head(b"GET / HTTP/1.0\r\n\r\n")

        assert head is not None and not wants_keep_alive(head)

    def test_connection_close_wins_over_http_11(self):
        head = parse_request_head(b"GET / HTTP/1.1\r\nConnection: close\r\n\r\n")

        assert head is not None and not wants_keep_alive(head)


@pytest.mark.asyncio
async def test_gateway_route_swaps_auth_and_captures_the_request(tmp_path: Path, certificates: BridgeCertificates):
    seen: list[httpx.Request] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=GATEWAY_BODY)

    bridge = _bridge(tmp_path, certificates, gateway_handler)
    async with _serving(bridge) as server:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{_port(server)}/v1/messages",
                headers={
                    "host": "api.anthropic.com",
                    "authorization": "Bearer sk-original-secret",
                    "content-type": "application/json",
                },
                content=b'{"model": "claude-sonnet-4-6"}',
            )

    assert response.status_code == 200
    assert response.json() == GATEWAY_BODY
    assert str(seen[0].url) == "http://gateway.test/v1/messages"
    assert seen[0].headers["authorization"] == "Bearer sk-gateway-key"
    assert "sk-original-secret" not in str(seen[0].headers)

    records = read_captures(tmp_path / "capture.jsonl")
    assert len(records) == 1
    assert records[0].upstream == "gateway"
    assert records[0].status_code == 200
    assert records[0].request_headers["authorization"].endswith("chars)")
    assert "sk-original-secret" not in records[0].request_headers["authorization"]


@pytest.mark.asyncio
async def test_passthrough_keeps_client_auth(tmp_path: Path, certificates: BridgeCertificates):
    seen: list[httpx.Request] = []

    def passthrough_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="pong")

    bridge = _bridge(tmp_path, certificates, passthrough_handler)
    async with _serving(bridge) as server:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{_port(server)}/v1/health",
                headers={
                    "host": "cloudcode-pa.googleapis.com",
                    "authorization": "Bearer oauth-token",
                },
            )

    assert response.status_code == 200
    assert response.text == "pong"
    assert str(seen[0].url) == "https://cloudcode-pa.googleapis.com/v1/health"
    assert seen[0].headers["authorization"] == "Bearer oauth-token"


@pytest.mark.asyncio
async def test_streamed_response_chunks_arrive_in_order(tmp_path: Path, certificates: BridgeCertificates):
    def stream_handler(request: httpx.Request) -> httpx.Response:
        async def chunks() -> "AsyncIterator[bytes]":
            yield b"data: one\n\n"
            yield b"data: two\n\n"

        return httpx.Response(200, content=chunks())

    bridge = _bridge(tmp_path, certificates, stream_handler)
    async with _serving(bridge) as server:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{_port(server)}/v1/messages",
                headers={"host": "api.anthropic.com"},
                content=b"{}",
            )

    assert response.status_code == 200
    assert response.text == "data: one\n\ndata: two\n\n"


@pytest.mark.asyncio
async def test_unreachable_upstream_answers_502(tmp_path: Path, certificates: BridgeCertificates):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    bridge = _bridge(tmp_path, certificates, failing_handler)
    async with _serving(bridge) as server:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{_port(server)}/v1/messages",
                headers={"host": "api.anthropic.com"},
                content=b"{}",
            )

    assert response.status_code == 502
    assert response.json() == {"error": "upstream unreachable"}


@pytest.mark.asyncio
async def test_keep_alive_serves_two_requests_on_one_connection(tmp_path: Path, certificates: BridgeCertificates):
    seen: list[httpx.Request] = []

    def counting_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"n": len(seen)})

    bridge = _bridge(tmp_path, certificates, counting_handler)
    async with _serving(bridge) as server:
        async with httpx.AsyncClient() as client:
            first = await client.post(
                f"http://127.0.0.1:{_port(server)}/v1/messages",
                headers={"host": "api.anthropic.com"},
                content=b"{}",
            )
            second = await client.post(
                f"http://127.0.0.1:{_port(server)}/v1/messages",
                headers={"host": "api.anthropic.com"},
                content=b"{}",
            )

    assert first.json() == {"n": 1}
    assert second.json() == {"n": 2}
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_chunked_request_body_is_reassembled(tmp_path: Path, certificates: BridgeCertificates):
    seen: list[httpx.Request] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    bridge = _bridge(tmp_path, certificates, gateway_handler)
    async with _serving(bridge) as server:
        reader, writer = await asyncio.open_connection("127.0.0.1", _port(server))
        writer.write(
            b"POST /v1/messages HTTP/1.1\r\n"
            b"Host: api.anthropic.com\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"5\r\nhello\r\n"
            b"6\r\n world\r\n"
            b"0\r\n"
            b"\r\n"
        )
        await writer.drain()
        raw = await reader.readuntil(b"0\r\n\r\n")
        writer.close()

    assert raw.startswith(b"HTTP/1.1 200")
    assert b"transfer-encoding: chunked" in raw
    assert seen[0].content == b"hello world"


@pytest.mark.asyncio
async def test_garbage_request_line_closes_the_connection(tmp_path: Path, certificates: BridgeCertificates):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    bridge = _bridge(tmp_path, certificates, handler)
    async with _serving(bridge) as server:
        reader, writer = await asyncio.open_connection("127.0.0.1", _port(server))
        writer.write(b"not a request line\r\n\r\n")
        await writer.drain()
        raw = await reader.read()
        writer.close()

    assert raw == b""

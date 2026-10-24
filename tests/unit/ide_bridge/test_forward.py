from litellm.ide_bridge.config import BridgeConfig
from litellm.ide_bridge.forward import (
    HOP_BY_HOP_REQUEST_HEADERS,
    GatewayRoute,
    build_outbound,
    decide_upstream,
    gateway_headers,
    passthrough_headers,
    passthrough_url,
)


def _config(**overrides: object) -> BridgeConfig:
    values: dict[str, object] = {
        "data_dir": "/tmp/bridge",
        "gateway_base_url": "http://127.0.0.1:4000",
        "gateway_api_key": "sk-gw",
        "route_hosts": ("api.anthropic.com",),
    }
    values.update(overrides)
    return BridgeConfig.model_validate(values)


class TestDecideUpstream:
    def test_routed_host_goes_to_the_gateway_with_full_path(self):
        target = decide_upstream("api.anthropic.com", "/v1/messages?beta=true", 443, _config())

        assert target == GatewayRoute(url="http://127.0.0.1:4000/v1/messages?beta=true")

    def test_everything_else_passes_through_with_its_port(self):
        target = decide_upstream("cloudcode-pa.googleapis.com", "/x", 8443, _config())

        assert target.host == "cloudcode-pa.googleapis.com"
        assert target.port == 8443


class TestPassthroughUrl:
    def test_default_port_is_omitted(self):
        target = decide_upstream("api.openai.com", "/v1/chat", 443, _config())

        assert passthrough_url(target, "/v1/chat") == "https://api.openai.com/v1/chat"

    def test_explicit_port_is_kept(self):
        target = decide_upstream("api.openai.com", "/v1/chat", 8443, _config())

        assert passthrough_url(target, "/v1/chat") == "https://api.openai.com:8443/v1/chat"


class TestGatewayHeaders:
    def test_inbound_auth_is_replaced_by_the_gateway_key(self):
        headers = gateway_headers(
            {
                "host": "api.anthropic.com",
                "authorization": "Bearer sk-original",
                "x-api-key": "sk-ant",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            "sk-gw",
        )

        assert headers["authorization"] == "Bearer sk-gw"
        assert "x-api-key" not in headers
        assert "host" not in headers
        assert headers["anthropic-version"] == "2023-06-01"
        assert headers["content-type"] == "application/json"


class TestPassthroughHeaders:
    def test_client_auth_survives_but_hop_by_hop_headers_do_not(self):
        headers = passthrough_headers(
            {
                "host": "cloudcode-pa.googleapis.com",
                "authorization": "Bearer oauth-token",
                "content-length": "123",
                "connection": "keep-alive",
                "transfer-encoding": "chunked",
                "content-type": "application/json",
            }
        )

        assert headers["authorization"] == "Bearer oauth-token"
        assert headers["content-type"] == "application/json"
        for forbidden in ("host", "content-length", "connection", "transfer-encoding"):
            assert forbidden not in headers

    def test_every_hop_by_hop_name_is_stripped(self):
        headers = passthrough_headers({name: "x" for name in HOP_BY_HOP_REQUEST_HEADERS})

        assert headers == {}


class TestBuildOutbound:
    def test_gateway_target_swaps_auth_and_labels_the_route(self):
        target = GatewayRoute(url="http://127.0.0.1:4000/v1/messages")

        outbound = build_outbound(
            target,
            "/v1/messages",
            {"authorization": "Bearer sk-original", "content-type": "application/json"},
            "sk-gw",
        )

        assert outbound is not None
        assert outbound.url == "http://127.0.0.1:4000/v1/messages"
        assert outbound.upstream_label == "gateway"
        assert outbound.headers["authorization"] == "Bearer sk-gw"

    def test_passthrough_target_keeps_the_clients_own_auth(self):
        target = decide_upstream("api.openai.com", "/v1/chat", 443, _config())

        outbound = build_outbound(
            target,
            "/v1/chat",
            {"authorization": "Bearer sk-original", "content-type": "application/json"},
            "sk-gw",
        )

        assert outbound is not None
        assert outbound.url == "https://api.openai.com/v1/chat"
        assert outbound.upstream_label == "passthrough"
        assert outbound.headers["authorization"] == "Bearer sk-original"

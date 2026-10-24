"""Upstream routing decisions for captured IDE requests.

A spoofed host either routes to the LiteLLM gateway (auth swapped for the
bridge key) or passes through to the real service untouched. Hop-by-hop
headers never cross the proxy: httpx re-frames the request, and re-emitting
upstream framing headers would corrupt the client's response.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, TypeAlias

from litellm.ide_bridge.config import BridgeConfig

HOP_BY_HOP_REQUEST_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "host",
    }
)

HOP_BY_HOP_RESPONSE_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authenticate-info",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "content-encoding",
    }
)

DEFAULT_HTTPS_PORT: Final[int] = 443


@dataclass(frozen=True, slots=True)
class Passthrough:
    """Forward to the real host, keeping the client's own auth."""

    host: str
    port: int


@dataclass(frozen=True, slots=True)
class GatewayRoute:
    """Forward to the LiteLLM gateway, swapping in the bridge key."""

    url: str


UpstreamTarget: TypeAlias = Passthrough | GatewayRoute


@dataclass(frozen=True, slots=True)
class OutboundRequest:
    """Everything ``server`` needs to send one request upstream."""

    url: str
    headers: Mapping[str, str]
    upstream_label: str


def decide_upstream(host: str, path: str, port: int, config: BridgeConfig) -> UpstreamTarget:
    if host in config.route_hosts:
        return GatewayRoute(url=f"{config.gateway_base_url}{path}")
    return Passthrough(host=host, port=port)


def passthrough_url(target: Passthrough, path: str) -> str:
    if target.port == DEFAULT_HTTPS_PORT:
        return f"https://{target.host}{path}"
    return f"https://{target.host}:{target.port}{path}"


def strip_forbidden(headers: Mapping[str, str], forbidden: frozenset[str]) -> Mapping[str, str]:
    return {name: value for name, value in headers.items() if name.lower() not in forbidden}


def passthrough_headers(original_headers: Mapping[str, str]) -> Mapping[str, str]:
    return strip_forbidden(original_headers, HOP_BY_HOP_REQUEST_HEADERS)


def gateway_headers(original_headers: Mapping[str, str], gateway_api_key: str) -> Mapping[str, str]:
    """Original headers minus inbound auth, plus the gateway bearer key."""
    stripped: Final = strip_forbidden(
        original_headers,
        HOP_BY_HOP_REQUEST_HEADERS | frozenset({"authorization", "x-api-key", "x-goog-api-key"}),
    )
    return {**stripped, "authorization": f"Bearer {gateway_api_key}"}


def build_outbound(
    target: UpstreamTarget,
    path: str,
    request_headers: Mapping[str, str],
    gateway_api_key: str,
) -> OutboundRequest | None:
    match target:
        case GatewayRoute(url=route_url):
            return OutboundRequest(
                url=route_url,
                headers=gateway_headers(request_headers, gateway_api_key),
                upstream_label="gateway",
            )
        case Passthrough():
            return OutboundRequest(
                url=passthrough_url(target, path),
                headers=passthrough_headers(request_headers),
                upstream_label="passthrough",
            )
    return None

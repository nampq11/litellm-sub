"""CLI request fingerprinting for subscription-backed upstreams.

Upstream providers that serve official CLI clients (claude-cli, the Antigravity
IDE backend) fingerprint incoming requests: HTTP header order, JSON body field
order, and the User-Agent identify the client that produced them. Requests
forwarded by a gateway arrive in whatever order the gateway's serializer chose,
which reads as third-party traffic to those upstreams and draws account flags.

A fingerprint pins the wire image of the official client, captured from
mitmproxy traces of the CLIs (registry data sourced from OmniRoute's
`open-sse/config/cliFingerprints.ts`, v3.8.52, MIT). Compatibility mode is
opt-in per provider through `LITELLM_CLI_COMPAT_PROVIDERS` (comma-separated) or
`LITELLM_CLI_COMPAT_ALL=1`.
"""

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from pydantic import TypeAdapter

ANTHROPIC_HEADER_ORDER: Final[tuple[str, ...]] = (
    "Accept",
    "Authorization",
    "Content-Type",
    "User-Agent",
    "X-Claude-Code-Session-Id",
    "X-Stainless-Arch",
    "X-Stainless-Lang",
    "X-Stainless-OS",
    "X-Stainless-Package-Version",
    "X-Stainless-Retry-Count",
    "X-Stainless-Runtime",
    "X-Stainless-Runtime-Version",
    "X-Stainless-Timeout",
    "anthropic-beta",
    "anthropic-dangerous-direct-browser-access",
    "anthropic-version",
    "x-app",
    "x-client-request-id",
    "Connection",
    "Host",
    "Accept-Encoding",
    "Content-Length",
)

ANTHROPIC_BODY_FIELD_ORDER: Final[tuple[str, ...]] = (
    "model",
    "messages",
    "system",
    "tools",
    "tool_choice",
    "metadata",
    "max_tokens",
    "temperature",
    "thinking",
    "context_management",
    "output_config",
    "stream",
)

ANTIGRAVITY_HEADER_ORDER: Final[tuple[str, ...]] = (
    "Accept",
    "Accept-Encoding",
    "Authorization",
    "Content-Type",
    "User-Agent",
    "x-goog-api-client",
    "x-client-name",
    "x-client-version",
    "x-machine-id",
    "x-vscode-sessionid",
    "Host",
    "Connection",
)

ANTIGRAVITY_BODY_FIELD_ORDER: Final[tuple[str, ...]] = (
    "project",
    "requestId",
    "request",
    "model",
    "userAgent",
    "requestType",
    "enabledCreditTypes",
)

DEFAULT_CLAUDE_CLI_USER_AGENT: Final[str] = "claude-cli/2.1.280 (external, cli)"

_TRUTHY: Final[frozenset[str]] = frozenset(("1", "true", "yes"))


@dataclass(frozen=True, slots=True)
class CliFingerprint:
    header_order: tuple[str, ...]
    body_field_order: tuple[str, ...]
    user_agent: str | None = None
    user_agent_env: str | None = None


CLI_FINGERPRINTS: Final[Mapping[str, CliFingerprint]] = MappingProxyType(
    {
        "anthropic": CliFingerprint(
            header_order=ANTHROPIC_HEADER_ORDER,
            body_field_order=ANTHROPIC_BODY_FIELD_ORDER,
            user_agent=DEFAULT_CLAUDE_CLI_USER_AGENT,
            user_agent_env="CLAUDE_USER_AGENT",
        ),
        "antigravity": CliFingerprint(
            header_order=ANTIGRAVITY_HEADER_ORDER,
            body_field_order=ANTIGRAVITY_BODY_FIELD_ORDER,
        ),
    }
)


def order_headers(headers: Mapping[str, str | bytes], order: Sequence[str]) -> Mapping[str, str | bytes]:
    by_lower: Final[Mapping[str, tuple[str, str | bytes]]] = {key.lower(): (key, headers[key]) for key in headers}
    front: Final[Mapping[str, str | bytes]] = {
        by_lower[wanted.lower()][0]: by_lower[wanted.lower()][1] for wanted in order if wanted.lower() in by_lower
    }
    order_lower: Final[frozenset[str]] = frozenset(name.lower() for name in order)
    rest: Final[Mapping[str, str | bytes]] = {
        key: value for key, value in headers.items() if key.lower() not in order_lower
    }
    return {**front, **rest}


def order_fields(body: Mapping[str, object], order: Sequence[str]) -> Mapping[str, object]:
    front: Final[Mapping[str, object]] = {key: body[key] for key in order if key in body}
    rest: Final[Mapping[str, object]] = {key: value for key, value in body.items() if key not in front}
    return {**front, **rest}


def cli_compat_providers() -> frozenset[str]:
    if os.getenv("LITELLM_CLI_COMPAT_ALL", "").strip().lower() in _TRUTHY:
        return frozenset(CLI_FINGERPRINTS)
    raw: Final[str] = os.getenv("LITELLM_CLI_COMPAT_PROVIDERS", "")
    requested: Final[frozenset[str]] = frozenset(part.strip().lower() for part in raw.split(",") if part.strip())
    return frozenset(name for name in requested if name in CLI_FINGERPRINTS)


def fingerprint_for(provider: str) -> CliFingerprint | None:
    key: Final[str] = provider.strip().lower()
    return CLI_FINGERPRINTS[key] if key in cli_compat_providers() else None


def _resolved_user_agent(fingerprint: CliFingerprint) -> str | None:
    if fingerprint.user_agent is None:
        return None
    if fingerprint.user_agent_env is None:
        return fingerprint.user_agent
    return os.getenv(fingerprint.user_agent_env) or fingerprint.user_agent


HEADERS_ADAPTER: Final = TypeAdapter(dict[str, str | bytes])
BODY_ADAPTER: Final = TypeAdapter(dict[str, object])


def compat_headers(provider: str, headers: Mapping[str, str | bytes]) -> Mapping[str, str | bytes]:
    fingerprint: Final[CliFingerprint | None] = fingerprint_for(provider)
    if fingerprint is None:
        return headers
    user_agent: Final[str | None] = _resolved_user_agent(fingerprint)
    with_agent: Final[Mapping[str, str | bytes]] = (
        {**headers, "User-Agent": user_agent} if user_agent is not None else headers
    )
    return order_headers(with_agent, fingerprint.header_order)


def compat_body(provider: str, data: Mapping[str, object]) -> str:
    fingerprint: Final[CliFingerprint | None] = fingerprint_for(provider)
    if fingerprint is None:
        return json.dumps(data)
    return json.dumps(order_fields(data, fingerprint.body_field_order))

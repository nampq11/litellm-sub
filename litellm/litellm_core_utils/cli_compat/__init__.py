"""Subscription CLI fingerprint compat for gateway-forwarded requests.

Fingerprinting upstreams (Anthropic's claude-cli endpoints) flag requests whose
header order, body field order, or User-Agent do not match the official client.
Set ``LITELLM_CLI_COMPAT_PROVIDERS=anthropic`` (or ``LITELLM_CLI_COMPAT_ALL=1``)
to pin the captured claude-cli wire image onto forwarded requests.
``CLAUDE_USER_AGENT`` overrides the pinned User-Agent string when it drifts.
"""

from typing import Final

from litellm.litellm_core_utils.cli_compat.ban_detection import (
    ACCOUNT_DEACTIVATED_SIGNALS,
    ban_cooldown_seconds,
    is_account_deactivated,
)
from litellm.litellm_core_utils.cli_compat.fingerprints import (
    BODY_ADAPTER,
    CLI_FINGERPRINTS,
    HEADERS_ADAPTER,
    CliFingerprint,
    compat_body,
    compat_headers,
    compat_wire,
    fingerprint_for,
    order_fields,
    order_headers,
)

__all__: Final[tuple[str, ...]] = (
    "ACCOUNT_DEACTIVATED_SIGNALS",
    "BODY_ADAPTER",
    "CLI_FINGERPRINTS",
    "HEADERS_ADAPTER",
    "CliFingerprint",
    "ban_cooldown_seconds",
    "compat_body",
    "compat_headers",
    "compat_wire",
    "fingerprint_for",
    "is_account_deactivated",
    "order_fields",
    "order_headers",
)

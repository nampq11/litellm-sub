"""Account-ban detection for subscription-backed upstreams.

Upstream providers that ban an account (suspension, deactivation, ToS
enforcement) keep returning the same ban wording on every request. A gateway
that treats those responses like any other error keeps routing traffic to a
dead account, which draws further enforcement. Matching the ban wording moves
the deployment into a long cooldown instead.

Signal list sourced from OmniRoute's `accountFallback.ts` (v3.8.52, MIT) plus
its ban-detection doc; transient wording ("rate limit", "verify your account")
is deliberately excluded so healthy accounts are never parked for a year.
"""

import os
from typing import Final

ACCOUNT_DEACTIVATED_SIGNALS: Final[frozenset[str]] = frozenset(
    (
        "account_deactivated",
        "account has been deactivated",
        "account has been disabled",
        "your account has been suspended",
        "this account is deactivated",
        "this service has been disabled in this account for violation",
        "this service has been disabled in this account",
    )
)

DEFAULT_BAN_COOLDOWN_SECONDS: Final[int] = 365 * 24 * 60 * 60


def is_account_deactivated(error_text: str) -> bool:
    lowered: Final[str] = error_text.lower()
    return any(signal in lowered for signal in ACCOUNT_DEACTIVATED_SIGNALS)


def ban_cooldown_seconds() -> int:
    raw: Final[str] = os.getenv("LITELLM_BAN_COOLDOWN_SECONDS", "")
    return int(raw) if raw.isdigit() else DEFAULT_BAN_COOLDOWN_SECONDS

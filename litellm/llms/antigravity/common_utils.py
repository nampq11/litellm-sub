import hashlib
import os
import time
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, TypedDict

from typing_extensions import ReadOnly

from litellm.llms.base_llm.chat.transformation import BaseLLMException

ANTIGRAVITY_API_BASE: Final[str] = "https://daily-cloudcode-pa.googleapis.com"
ANTIGRAVITY_ENDPOINT_FALLBACKS: Final[tuple[str, ...]] = (
    "https://daily-cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
)

ANTIGRAVITY_TOKEN_URL: Final[str] = "https://oauth2.googleapis.com/token"
ANTIGRAVITY_AUTH_URL: Final[str] = "https://accounts.google.com/o/oauth2/v2/auth"
ANTIGRAVITY_USERINFO_URL: Final[str] = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"

ANTIGRAVITY_CLIENT_ID: Final[str] = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
ANTIGRAVITY_CLIENT_SECRET: Final[str] = "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f"

DEFAULT_ANTIGRAVITY_USER_AGENT: Final[str] = (
    "antigravity/cli/1.4.0 (aidev_client; os_type=linux; arch=amd64; cl=982146307; auth_method=consumer)"
)

ANTIGRAVITY_SCOPES: Final[tuple[str, ...]] = (
    "https://www.googleapis.com/auth/aicode",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
)

ANTIGRAVITY_MODEL_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {
        "gemini-3.8-flash": "gemini-3.8-flash-low",
        "gemini-3.8-flash-low": "gemini-3.8-flash-low",
        "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
        "gemini-3.8-flash-high": "gemini-3.8-flash-high",
        "gemini-3.7-flash": "gemini-3.7-flash-low",
        "gemini-3.7-flash-low": "gemini-3.7-flash-low",
        "gemini-3.7-flash-medium": "gemini-3.7-flash-medium",
        "gemini-3.7-flash-high": "gemini-3.7-flash-high",
        "gemini-3.6-flash": "gemini-3.6-flash-low",
        "gemini-3.6-flash-low": "gemini-3.6-flash-low",
        "gemini-3.6-flash-medium": "gemini-3.6-flash-medium",
        "gemini-3.6-flash-high": "gemini-3.6-flash-high",
        "gemini-3.5-flash": "gemini-3.5-flash-extra-low",
        "gemini-3.5-flash-extra-low": "gemini-3.5-flash-extra-low",
        "gemini-3.5-flash-low": "gemini-3.5-flash-low",
        "gemini-3.5-flash-high": "gemini-3-flash-agent",
        "gemini-3.1-pro": "gemini-3.1-pro-low",
        "gemini-3.1-pro-low": "gemini-3.1-pro-low",
        "gemini-3.1-pro-high": "gemini-pro-agent",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "claude-opus-4-6": "claude-opus-4-6-thinking",
        "gpt-oss-120b": "gpt-oss-120b-medium",
        "claude-3-7-sonnet": "claude-sonnet-4-6",
        "claude-3-5-sonnet": "claude-sonnet-4-6",
        "gemini-2.5-flash": "gemini-3.5-flash-low",
        "gemini-2.5-pro": "gemini-3.1-pro-low",
    }
)


class AntigravityError(BaseLLMException):
    pass


class AntigravityAuthError(AntigravityError):
    pass


class AntigravityQuotaExceededError(AntigravityError):
    pass


class AntigravityEnvelopeLabels(TypedDict):
    last_step_index: ReadOnly[str]
    request_id: ReadOnly[str]
    trajectory_id: ReadOnly[str]
    used_claude: ReadOnly[str]
    used_claude_conservative: ReadOnly[str]
    used_non_gemini_model: ReadOnly[str]


class AntigravityEnvelope(TypedDict):
    request_id: ReadOnly[str]
    session_id: ReadOnly[str]
    labels: ReadOnly[AntigravityEnvelopeLabels]


def map_antigravity_model(model: str) -> str:
    cleaned: Final[str] = model.lower().removeprefix("antigravity/")
    return ANTIGRAVITY_MODEL_MAP.get(cleaned, cleaned)


def stable_project_id(seed: str) -> str:
    digest: Final[bytes] = hashlib.sha1(f"antigravity:{seed}".encode()).digest()[:16]
    b6: Final[int] = (digest[6] & 0x0F) | 0x50
    b8: Final[int] = (digest[8] & 0x3F) | 0x80
    mut: Final[bytearray] = bytearray(digest)
    mut[6] = b6
    mut[8] = b8
    hex_str: Final[str] = mut.hex()
    return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:32]}"


def default_project_id(seed: str = "antigravity-default") -> str:
    configured: Final[str | None] = os.getenv("ANTIGRAVITY_PROJECT_ID")
    if configured and configured.strip():
        return configured.strip()
    return stable_project_id(seed)


def antigravity_request_envelope(
    model_id: str,
    step: int = 1,
    trajectory_id: str | None = None,
    session_id: str | None = None,
) -> AntigravityEnvelope:
    resolved_traj_id: Final[str] = trajectory_id or str(uuid.uuid4())
    resolved_session_id: Final[str] = session_id or str(int(time.time() * 1000))
    resolved_step: Final[int] = max(1, step)
    is_claude: Final[bool] = "claude" in model_id.lower()
    is_non_gemini: Final[bool] = is_claude or "gpt-oss" in model_id.lower()

    claude_flag: Final[str] = "true" if is_claude else "false"
    non_gemini_flag: Final[str] = "true" if is_non_gemini else "false"

    labels: Final[AntigravityEnvelopeLabels] = {
        "last_step_index": str(max(0, resolved_step - 1)),
        "request_id": f"{resolved_traj_id}-{max(0, resolved_step - 1)}",
        "trajectory_id": resolved_traj_id,
        "used_claude": claude_flag,
        "used_claude_conservative": claude_flag,
        "used_non_gemini_model": non_gemini_flag,
    }

    full_request_id: Final[str] = f"agent/{uuid.uuid4()}/{int(time.time() * 1000)}/{resolved_traj_id}/{resolved_step}"

    return {
        "request_id": full_request_id,
        "session_id": resolved_session_id,
        "labels": labels,
    }


def get_antigravity_headers(token: str) -> Mapping[str, str]:
    user_agent: Final[str] = os.getenv("ANTIGRAVITY_USER_AGENT") or DEFAULT_ANTIGRAVITY_USER_AGENT
    return MappingProxyType(
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        }
    )

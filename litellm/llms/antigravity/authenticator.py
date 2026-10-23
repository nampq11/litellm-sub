import json
import os
import threading
import time
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, TypeAlias, TypedDict

import httpx
from pydantic import JsonValue, TypeAdapter
from typing_extensions import ReadOnly

from litellm._logging import verbose_logger
from litellm.constants import HTTP_HANDLER_CONNECT_TIMEOUT_SECONDS
from litellm.llms.custom_httpx.http_handler import (
    HTTPHandler,
    get_httpx_client,  # pyright: ignore[reportUnknownVariableType]  # get_httpx_client signature
)

from .common_utils import (
    ANTIGRAVITY_CLIENT_ID,
    ANTIGRAVITY_CLIENT_SECRET,
    ANTIGRAVITY_TOKEN_URL,
    AntigravityAuthError,
    default_project_id,
)

TOKEN_REFRESH_SKEW_MS: Final[int] = 5 * 60 * 1000
DEFAULT_COOLDOWN_SECONDS: Final[float] = 300.0

JsonObject: TypeAlias = Mapping[str, JsonValue]
_JSON_OBJECT_ADAPTER: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)


class StoredAccount(TypedDict):
    accountId: ReadOnly[str]
    access: ReadOnly[str]
    refresh: ReadOnly[str]
    expires: ReadOnly[int]
    email: ReadOnly[str | None]
    projectId: ReadOnly[str | None]
    lastUsedAt: ReadOnly[int]
    rateLimitedUntil: ReadOnly[float | None]


def _optional_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: JsonValue | None) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _optional_float(value: JsonValue | None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_account_entry(account_id: str, raw: JsonObject) -> StoredAccount | None:
    access_val: Final[str | None] = _optional_str(raw.get("access")) or _optional_str(raw.get("access_token"))
    refresh_val: Final[str | None] = _optional_str(raw.get("refresh")) or _optional_str(raw.get("refresh_token"))
    if access_val is None or refresh_val is None:
        return None

    expires_val: Final[int] = _optional_int(raw.get("expires")) or _optional_int(raw.get("expires_at")) or 0
    last_used_val: Final[int] = (
        _optional_int(raw.get("lastUsedAt")) or _optional_int(raw.get("last_used_at")) or 0
    )
    email_val: Final[str | None] = _optional_str(raw.get("email"))
    project_val: Final[str | None] = _optional_str(raw.get("projectId")) or _optional_str(raw.get("project_id"))
    rl_val: Final[float | None] = _optional_float(raw.get("rateLimitedUntil")) or _optional_float(
        raw.get("rate_limited_until")
    )

    return {
        "accountId": account_id,
        "access": access_val,
        "refresh": refresh_val,
        "expires": expires_val,
        "email": email_val,
        "projectId": project_val,
        "lastUsedAt": last_used_val,
        "rateLimitedUntil": rl_val,
    }


def _with_rate_limit(account: StoredAccount, cooldown_target: float) -> StoredAccount:
    return {
        "accountId": account["accountId"],
        "access": account["access"],
        "refresh": account["refresh"],
        "expires": account["expires"],
        "email": account["email"],
        "projectId": account["projectId"],
        "lastUsedAt": account["lastUsedAt"],
        "rateLimitedUntil": cooldown_target,
    }


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class Authenticator:
    def __init__(self) -> None:
        self._lock: Final[threading.Lock] = threading.Lock()
        self.token_dir: Final[str] = os.getenv(
            "ANTIGRAVITY_TOKEN_DIR",
            os.path.expanduser("~/.config/litellm/antigravity"),
        )
        self.accounts_file: Final[str] = os.getenv(
            "ANTIGRAVITY_ACCOUNTS_FILE",
            os.path.join(self.token_dir, "accounts.json"),
        )
        self.pi_accounts_file: Final[str] = os.path.expanduser("~/.pi/agent/antigravity-accounts.json")
        self._ensure_token_dir()

    def _ensure_token_dir(self) -> None:
        if not os.path.exists(self.token_dir):
            try:
                os.makedirs(self.token_dir, exist_ok=True)
            except OSError as exc:
                verbose_logger.warning("Could not create Antigravity token dir: %s", exc)

    def _candidate_files(self) -> tuple[str, ...]:
        if self.pi_accounts_file != self.accounts_file:
            return (self.accounts_file, self.pi_accounts_file)
        return (self.accounts_file,)

    def load_accounts(self) -> Mapping[str, StoredAccount]:
        for path in self._candidate_files():
            if not os.path.exists(path):
                continue
            try:
                parsed_data: JsonObject = _JSON_OBJECT_ADAPTER.validate_json(_read_file(path))
                accounts_raw_val = parsed_data.get("accounts")
                if not isinstance(accounts_raw_val, Mapping):
                    continue
                accounts_map: JsonObject = accounts_raw_val
                loaded_accounts: dict[str, StoredAccount] = {}  # mutable-ok: builder for MappingProxyType
                for key, val in accounts_map.items():
                    if isinstance(val, Mapping):
                        parsed_acc: StoredAccount | None = _parse_account_entry(key, val)
                        if parsed_acc is not None:
                            loaded_accounts[parsed_acc["accountId"]] = parsed_acc
                if loaded_accounts:
                    return MappingProxyType(loaded_accounts)
            except (OSError, ValueError) as exc:
                verbose_logger.warning("Error reading accounts from %s: %s", path, exc)

        env_access: Final[str | None] = os.getenv("ANTIGRAVITY_ACCESS_TOKEN")
        env_refresh: Final[str | None] = os.getenv("ANTIGRAVITY_REFRESH_TOKEN")
        if env_access and env_refresh:
            env_account: Final[StoredAccount] = {
                "accountId": "env-account",
                "access": env_access,
                "refresh": env_refresh,
                "expires": int(time.time() * 1000) + 3600 * 1000,
                "email": os.getenv("ANTIGRAVITY_EMAIL"),
                "projectId": os.getenv("ANTIGRAVITY_PROJECT_ID"),
                "lastUsedAt": int(time.time() * 1000),
                "rateLimitedUntil": None,
            }
            return MappingProxyType({"env-account": env_account})

        return MappingProxyType({})

    def save_accounts(self, accounts: Mapping[str, StoredAccount], active_id: str | None = None) -> None:
        self._ensure_token_dir()
        accounts_dict: Final[dict[str, object]] = {  # mutable-ok: JSON serializable dict
            acc_id: dict(acc) for acc_id, acc in accounts.items()
        }
        target_path: Final[str] = (
            self.accounts_file
            if os.path.exists(self.accounts_file) or not os.path.exists(self.pi_accounts_file)
            else self.pi_accounts_file
        )
        temp_path: Final[str] = f"{target_path}.{os.getpid()}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                payload_str: Final[str] = json.dumps(
                    {
                        "version": 1,
                        "activeAccountId": active_id,
                        "accounts": accounts_dict,
                    },
                    indent=2,
                )
                f.write(payload_str)
            os.replace(temp_path, target_path)
        except OSError as exc:
            verbose_logger.warning("Could not persist Antigravity accounts to %s: %s", target_path, exc)

    def _refresh_tokens(self, refresh_token: str) -> JsonObject:
        client: Final[HTTPHandler] = (  # pyright: ignore[reportUnknownVariableType]  # get_httpx_client return signature
            get_httpx_client()
        )
        response: Final[httpx.Response] = client.post(  # pyright: ignore[reportUnknownMemberType]  # HTTPHandler.post signature
            ANTIGRAVITY_TOKEN_URL,
            data={
                "client_id": ANTIGRAVITY_CLIENT_ID,
                "client_secret": ANTIGRAVITY_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=httpx.Timeout(30.0, connect=HTTP_HANDLER_CONNECT_TIMEOUT_SECONDS),
        )
        if response.status_code != 200:
            raise AntigravityAuthError(
                status_code=response.status_code,
                message=f"Failed to refresh Antigravity token: {response.text}",
            )
        return _JSON_OBJECT_ADAPTER.validate_json(response.text)

    def _ensure_fresh(self, account: StoredAccount) -> StoredAccount:
        now_ms: Final[int] = int(time.time() * 1000)
        if account["expires"] > now_ms + TOKEN_REFRESH_SKEW_MS:
            return account

        token_data: Final[JsonObject] = self._refresh_tokens(account["refresh"])
        new_access_val: Final[str | None] = _optional_str(token_data.get("access_token"))
        new_access: Final[str] = new_access_val if new_access_val is not None else account["access"]
        expires_in_val: Final[int | None] = _optional_int(token_data.get("expires_in"))
        expires_in_sec: Final[int] = expires_in_val if expires_in_val is not None else 3600

        updated: Final[StoredAccount] = {
            "accountId": account["accountId"],
            "access": new_access,
            "refresh": account["refresh"],
            "expires": now_ms + (expires_in_sec * 1000),
            "email": account["email"],
            "projectId": account["projectId"],
            "lastUsedAt": account["lastUsedAt"],
            "rateLimitedUntil": account["rateLimitedUntil"],
        }
        return updated

    def get_active_credential(self) -> tuple[str, str]:
        with self._lock:
            accounts: Final[Mapping[str, StoredAccount]] = self.load_accounts()
            if not accounts:
                env_tok: Final[str | None] = os.getenv("ANTIGRAVITY_ACCESS_TOKEN")
                if env_tok:
                    return env_tok, default_project_id()
                raise AntigravityAuthError(
                    status_code=401,
                    message="No Antigravity accounts configured. Run `pi login antigravity` or set ANTIGRAVITY_ACCESS_TOKEN.",
                )

            now_sec: Final[float] = time.time()
            available: Final[tuple[StoredAccount, ...]] = tuple(
                acc
                for acc in accounts.values()
                if acc["rateLimitedUntil"] is None or acc["rateLimitedUntil"] <= now_sec
            )

            candidate: Final[StoredAccount] = (
                min(available, key=lambda a: a["lastUsedAt"])
                if available
                else min(accounts.values(), key=lambda a: a["rateLimitedUntil"] or 0.0)
            )

            fresh: Final[StoredAccount] = self._ensure_fresh(candidate)
            now_ms: Final[int] = int(time.time() * 1000)
            active: Final[StoredAccount] = {
                "accountId": fresh["accountId"],
                "access": fresh["access"],
                "refresh": fresh["refresh"],
                "expires": fresh["expires"],
                "email": fresh["email"],
                "projectId": fresh["projectId"],
                "lastUsedAt": now_ms,
                "rateLimitedUntil": fresh["rateLimitedUntil"],
            }

            mut_accounts: Final[Mapping[str, StoredAccount]] = MappingProxyType(
                {**accounts, active["accountId"]: active}
            )
            self.save_accounts(mut_accounts, active_id=active["accountId"])

            project_id: Final[str] = active["projectId"] or default_project_id(active["email"] or active["accountId"])
            return active["access"], project_id

    def failover_to_next_account(
        self,
        tried_tokens: frozenset[str],
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> tuple[str, str] | None:
        with self._lock:
            accounts: Final[Mapping[str, StoredAccount]] = self.load_accounts()
            if not accounts:
                return None

            now_sec: Final[float] = time.time()
            now_ms: Final[int] = int(now_sec * 1000)
            cooldown_target: Final[float] = now_sec + cooldown_seconds

            mut_accounts: Final[Mapping[str, StoredAccount]] = MappingProxyType(
                {
                    acc_id: _with_rate_limit(acc, cooldown_target) if acc["access"] in tried_tokens else acc
                    for acc_id, acc in accounts.items()
                }
            )

            untried: Final[tuple[StoredAccount, ...]] = tuple(
                acc for acc in mut_accounts.values() if acc["access"] not in tried_tokens
            )
            if not untried:
                self.save_accounts(mut_accounts)
                return None

            candidate: Final[StoredAccount] = min(untried, key=lambda a: a["lastUsedAt"])
            fresh: Final[StoredAccount] = self._ensure_fresh(candidate)
            activated: Final[StoredAccount] = {
                "accountId": fresh["accountId"],
                "access": fresh["access"],
                "refresh": fresh["refresh"],
                "expires": fresh["expires"],
                "email": fresh["email"],
                "projectId": fresh["projectId"],
                "lastUsedAt": now_ms,
                "rateLimitedUntil": None,
            }
            updated_accounts: Final[Mapping[str, StoredAccount]] = MappingProxyType(
                {**mut_accounts, activated["accountId"]: activated}
            )
            self.save_accounts(updated_accounts, active_id=activated["accountId"])

            project_id: Final[str] = activated["projectId"] or default_project_id(
                activated["email"] or activated["accountId"]
            )
            return activated["access"], project_id

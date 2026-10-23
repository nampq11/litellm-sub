import json
import os
import time
from unittest.mock import patch

import pytest

from litellm.llms.antigravity.authenticator import Authenticator


class TestAntigravityAuthenticator:
    @pytest.fixture
    def authenticator(self, tmp_path, monkeypatch):
        token_dir = str(tmp_path / "antigravity")
        accounts_file = os.path.join(token_dir, "accounts.json")
        monkeypatch.setenv("ANTIGRAVITY_TOKEN_DIR", token_dir)
        monkeypatch.setenv("ANTIGRAVITY_ACCOUNTS_FILE", accounts_file)
        monkeypatch.delenv("ANTIGRAVITY_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("ANTIGRAVITY_REFRESH_TOKEN", raising=False)
        return Authenticator()

    def test_load_accounts_from_file(self, authenticator):
        now_ms = int(time.time() * 1000)
        data = {
            "version": 1,
            "activeAccountId": "acc-1",
            "accounts": {
                "acc-1": {
                    "accountId": "acc-1",
                    "access": "tok-1",
                    "refresh": "ref-1",
                    "expires": now_ms + 3600000,
                    "email": "user1@example.com",
                    "projectId": "proj-1",
                    "lastUsedAt": now_ms - 1000,
                },
                "acc-2": {
                    "accountId": "acc-2",
                    "access": "tok-2",
                    "refresh": "ref-2",
                    "expires": now_ms + 3600000,
                    "email": "user2@example.com",
                    "projectId": "proj-2",
                    "lastUsedAt": now_ms - 5000,
                },
            },
        }
        with open(authenticator.accounts_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        loaded = authenticator.load_accounts()
        assert len(loaded) == 2
        assert "acc-1" in loaded
        assert "acc-2" in loaded
        assert loaded["acc-1"]["access"] == "tok-1"
        assert loaded["acc-2"]["email"] == "user2@example.com"

    def test_get_active_credential_least_recently_used(self, authenticator):
        now_ms = int(time.time() * 1000)
        data = {
            "version": 1,
            "accounts": {
                "acc-newer": {
                    "accountId": "acc-newer",
                    "access": "tok-newer",
                    "refresh": "ref-newer",
                    "expires": now_ms + 3600000,
                    "email": "newer@example.com",
                    "projectId": "proj-newer",
                    "lastUsedAt": now_ms - 100,
                },
                "acc-older": {
                    "accountId": "acc-older",
                    "access": "tok-older",
                    "refresh": "ref-older",
                    "expires": now_ms + 3600000,
                    "email": "older@example.com",
                    "projectId": "proj-older",
                    "lastUsedAt": now_ms - 10000,
                },
            },
        }
        with open(authenticator.accounts_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        token, project_id = authenticator.get_active_credential()
        assert token == "tok-older"
        assert project_id == "proj-older"

    def test_get_active_credential_refreshes_expired(self, authenticator):
        now_ms = int(time.time() * 1000)
        data = {
            "version": 1,
            "accounts": {
                "acc-exp": {
                    "accountId": "acc-exp",
                    "access": "tok-expired",
                    "refresh": "ref-valid",
                    "expires": now_ms - 1000,
                    "email": "exp@example.com",
                    "projectId": "proj-exp",
                    "lastUsedAt": 0,
                }
            },
        }
        with open(authenticator.accounts_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        refreshed_payload = {
            "access_token": "tok-fresh",
            "expires_in": 3600,
        }

        with patch.object(authenticator, "_refresh_tokens", return_value=refreshed_payload):
            token, project_id = authenticator.get_active_credential()
            assert token == "tok-fresh"
            assert project_id == "proj-exp"

    def test_failover_to_next_account(self, authenticator):
        now_ms = int(time.time() * 1000)
        data = {
            "version": 1,
            "accounts": {
                "acc-1": {
                    "accountId": "acc-1",
                    "access": "tok-1",
                    "refresh": "ref-1",
                    "expires": now_ms + 3600000,
                    "email": "acc1@example.com",
                    "projectId": "proj-1",
                    "lastUsedAt": now_ms - 1000,
                },
                "acc-2": {
                    "accountId": "acc-2",
                    "access": "tok-2",
                    "refresh": "ref-2",
                    "expires": now_ms + 3600000,
                    "email": "acc2@example.com",
                    "projectId": "proj-2",
                    "lastUsedAt": now_ms - 500,
                },
            },
        }
        with open(authenticator.accounts_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        failover_res = authenticator.failover_to_next_account(frozenset({"tok-1"}))
        assert failover_res is not None
        next_tok, next_proj = failover_res
        assert next_tok == "tok-2"
        assert next_proj == "proj-2"

        updated = authenticator.load_accounts()
        assert updated["acc-1"]["rateLimitedUntil"] is not None

    def test_failover_when_all_accounts_exhausted_returns_none(self, authenticator):
        now_ms = int(time.time() * 1000)
        data = {
            "version": 1,
            "accounts": {
                "acc-1": {
                    "accountId": "acc-1",
                    "access": "tok-1",
                    "refresh": "ref-1",
                    "expires": now_ms + 3600000,
                    "lastUsedAt": now_ms,
                }
            },
        }
        with open(authenticator.accounts_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        res = authenticator.failover_to_next_account(frozenset({"tok-1"}))
        assert res is None

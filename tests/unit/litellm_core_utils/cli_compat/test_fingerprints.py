import json
from typing import Final

import pytest

from litellm.litellm_core_utils.cli_compat import (
    CLI_FINGERPRINTS,
    compat_body,
    compat_headers,
    fingerprint_for,
    order_fields,
    order_headers,
)


class TestOrderFields:
    def test_registered_keys_move_to_the_front_in_order(self):
        body = {"stream": True, "unknown_extra": 1, "model": "claude-sonnet-4-6", "messages": []}

        ordered = order_fields(body, ("model", "messages", "stream"))

        assert tuple(ordered) == ("model", "messages", "stream", "unknown_extra")
        assert ordered["stream"] is True

    def test_registered_keys_missing_from_the_body_are_skipped(self):
        ordered = order_fields({"temperature": 1}, ("model", "temperature"))

        assert tuple(ordered) == ("temperature",)

    def test_body_without_registered_keys_keeps_its_own_order(self):
        ordered = order_fields({"b": 1, "a": 2}, ("model",))

        assert tuple(ordered) == ("b", "a")


class TestOrderHeaders:
    def test_registered_headers_match_case_insensitively_keeping_original_casing(self):
        headers = {"anthropic-version": "2023-06-01", "content-type": "application/json", "x-app": "cli"}

        ordered = order_headers(headers, ("Content-Type", "X-App", "anthropic-version"))

        assert tuple(ordered) == ("content-type", "x-app", "anthropic-version")

    def test_unlisted_headers_are_appended_after_the_registered_ones(self):
        headers = {"z-header": "1", "a-header": "2", "User-Agent": "cli"}

        ordered = order_headers(headers, ("User-Agent",))

        assert tuple(ordered) == ("User-Agent", "z-header", "a-header")


class TestEnabledProviders:
    def test_compat_is_off_by_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LITELLM_CLI_COMPAT_PROVIDERS", raising=False)
        monkeypatch.delenv("LITELLM_CLI_COMPAT_ALL", raising=False)

        assert fingerprint_for("anthropic") is None

    def test_provider_list_enables_only_registered_providers(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LITELLM_CLI_COMPAT_ALL", raising=False)
        monkeypatch.setenv("LITELLM_CLI_COMPAT_PROVIDERS", "Anthropic, unknown-router")

        assert fingerprint_for("anthropic") is not None
        assert fingerprint_for("unknown-router") is None

    def test_all_flag_enables_every_registered_provider(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LITELLM_CLI_COMPAT_PROVIDERS", raising=False)
        monkeypatch.setenv("LITELLM_CLI_COMPAT_ALL", "1")

        enabled: Final[frozenset[str]] = frozenset(
            provider for provider in CLI_FINGERPRINTS if fingerprint_for(provider) is not None
        )

        assert enabled == frozenset(CLI_FINGERPRINTS)


class TestCompatHeaders:
    def test_anthropic_headers_are_reordered_and_user_agent_injected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LITELLM_CLI_COMPAT_PROVIDERS", "anthropic")
        monkeypatch.delenv("CLAUDE_USER_AGENT", raising=False)
        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "x-api-key": "sk-test",
            "accept": "application/json",
        }

        ordered = compat_headers("anthropic", headers)

        assert tuple(ordered)[:2] == ("accept", "content-type")
        assert ordered["User-Agent"] == "claude-cli/2.1.280 (external, cli)"

    def test_user_agent_env_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LITELLM_CLI_COMPAT_PROVIDERS", "anthropic")
        monkeypatch.setenv("CLAUDE_USER_AGENT", "claude-cli/9.9.9 (external, cli)")

        ordered = compat_headers("anthropic", {"content-type": "application/json"})

        assert ordered["User-Agent"] == "claude-cli/9.9.9 (external, cli)"

    def test_antigravity_keeps_the_provider_user_agent(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LITELLM_CLI_COMPAT_PROVIDERS", "antigravity")
        headers = {"User-Agent": "antigravity/cli/1.4.0", "content-type": "application/json"}

        ordered = compat_headers("antigravity", headers)

        assert ordered["User-Agent"] == "antigravity/cli/1.4.0"

    def test_disabled_provider_returns_the_same_headers(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LITELLM_CLI_COMPAT_PROVIDERS", raising=False)
        monkeypatch.delenv("LITELLM_CLI_COMPAT_ALL", raising=False)
        headers = {"content-type": "application/json"}

        assert compat_headers("anthropic", headers) is headers


class TestCompatBody:
    def test_anthropic_body_serializes_with_cli_field_order(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LITELLM_CLI_COMPAT_PROVIDERS", "anthropic")
        data = {"stream": True, "max_tokens": 64, "model": "claude-sonnet-4-6", "messages": []}

        serialized = compat_body("anthropic", data)

        assert list(json.loads(serialized)) == ["model", "messages", "max_tokens", "stream"]

    def test_disabled_provider_serializes_without_reordering(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LITELLM_CLI_COMPAT_PROVIDERS", raising=False)
        monkeypatch.delenv("LITELLM_CLI_COMPAT_ALL", raising=False)
        data = {"stream": True, "model": "claude-sonnet-4-6"}

        assert json.loads(compat_body("anthropic", data)) == data

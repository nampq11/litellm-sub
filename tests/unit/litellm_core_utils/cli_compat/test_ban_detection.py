import pytest

from litellm.litellm_core_utils.cli_compat import (
    ACCOUNT_DEACTIVATED_SIGNALS,
    ban_cooldown_seconds,
    is_account_deactivated,
)


class TestIsAccountDeactivated:
    @pytest.mark.parametrize("signal", sorted(ACCOUNT_DEACTIVATED_SIGNALS))
    def test_detects_every_ban_signal_inside_a_realistic_error_body(self, signal: str):
        error_body = f'{{"error": {{"message": "Upstream said: {signal}", "type": "forbidden"}}}}'

        assert is_account_deactivated(error_body)

    def test_detection_is_case_insensitive(self):
        assert is_account_deactivated("Your Account Has Been Suspended")

    @pytest.mark.parametrize(
        "transient_error",
        [
            "rate limit exceeded, retry in 60s",
            "verify your account to continue",
            "insufficient_quota: upgrade your plan",
            "credit_balance_too_low",
        ],
    )
    def test_transient_wording_is_not_a_ban(self, transient_error: str):
        assert not is_account_deactivated(transient_error)

    def test_empty_error_text_is_not_a_ban(self):
        assert not is_account_deactivated("")


class TestBanCooldownSeconds:
    def test_default_cooldown_is_one_year(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LITELLM_BAN_COOLDOWN_SECONDS", raising=False)

        assert ban_cooldown_seconds() == 365 * 24 * 60 * 60

    def test_operator_can_shorten_the_cooldown(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LITELLM_BAN_COOLDOWN_SECONDS", "3600")

        assert ban_cooldown_seconds() == 3600

    def test_non_numeric_value_falls_back_to_the_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LITELLM_BAN_COOLDOWN_SECONDS", "soon")

        assert ban_cooldown_seconds() == 365 * 24 * 60 * 60

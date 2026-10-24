import pytest
from pydantic import ValidationError

from litellm.ide_bridge.config import (
    BridgeConfig,
    bridge_config_from_env,
    parse_bool,
    parse_host_list,
)


def _config(**overrides: object) -> BridgeConfig:
    values: dict[str, object] = {"data_dir": "/tmp/bridge"}
    values.update(overrides)
    return BridgeConfig.model_validate(values)


class TestDefaults:
    def test_empty_env_gives_the_documented_defaults(self):
        config = bridge_config_from_env({})

        assert config.listen_host == "127.0.0.1"
        assert config.listen_port == 443
        assert config.gateway_base_url == "http://127.0.0.1:4000"
        assert config.route_hosts == ("api.anthropic.com",)
        assert config.gateway_api_key is None
        assert config.install_trust is True

    def test_spoof_hosts_union_is_deduplicated(self):
        config = _config(
            route_hosts=("api.anthropic.com", "api.openai.com"),
            passthrough_hosts=("api.anthropic.com", "cloudcode-pa.googleapis.com"),
        )

        assert config.spoof_hosts == (
            "api.anthropic.com",
            "api.openai.com",
            "cloudcode-pa.googleapis.com",
        )


class TestEnvOverrides:
    def test_all_env_variables_are_honoured(self):
        config = bridge_config_from_env(
            {
                "LITELLM_BRIDGE_DATA_DIR": "/var/lib/bridge",
                "LITELLM_BRIDGE_HOST": "0.0.0.0",
                "LITELLM_BRIDGE_PORT": "8443",
                "LITELLM_BRIDGE_GATEWAY_URL": "http://gateway:4000/",
                "LITELLM_BRIDGE_API_KEY": "sk-gw",
                "LITELLM_BRIDGE_ROUTE_HOSTS": "api.anthropic.com, Api.OpenAI.com ,",
                "LITELLM_BRIDGE_PASSTHROUGH_HOSTS": "cloudcode-pa.googleapis.com",
                "LITELLM_BRIDGE_INSTALL_TRUST": "0",
            }
        )

        assert str(config.data_dir) == "/var/lib/bridge"
        assert config.listen_host == "0.0.0.0"
        assert config.listen_port == 8443
        assert config.gateway_base_url == "http://gateway:4000"
        assert config.gateway_api_key == "sk-gw"
        assert config.route_hosts == ("api.anthropic.com", "api.openai.com")
        assert config.passthrough_hosts == ("cloudcode-pa.googleapis.com",)
        assert config.install_trust is False

    def test_non_numeric_port_raises(self):
        with pytest.raises(ValueError, match="LITELLM_BRIDGE_PORT"):
            bridge_config_from_env({"LITELLM_BRIDGE_PORT": "https"})


class TestModelInvariants:
    def test_config_is_frozen(self):
        config = _config()

        with pytest.raises(ValidationError):
            config.listen_port = 8443

    def test_port_must_be_a_valid_tcp_port(self):
        with pytest.raises(ValidationError):
            _config(listen_port=70000)

    def test_derived_paths_live_under_the_data_dir(self):
        config = _config(data_dir="/var/lib/bridge")

        assert str(config.ca_cert_path) == "/var/lib/bridge/ca.crt"
        assert str(config.leaves_dir) == "/var/lib/bridge/leaves"
        assert str(config.pidfile_path) == "/var/lib/bridge/bridge.pid"
        assert str(config.capture_path) == "/var/lib/bridge/capture.jsonl"


class TestParsers:
    def test_host_list_strips_and_dedupes(self):
        assert parse_host_list(" A.com , b.com,A.COM ,") == ("a.com", "b.com")

    def test_bool_accepts_common_truthy_spellings(self):
        for raw in ("1", "true", "YES", "on"):
            assert parse_bool(raw)
        for raw in ("0", "false", "no", "off", ""):
            assert not parse_bool(raw)

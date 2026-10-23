"""Configuration for the IDE bridge.

Frozen pydantic model, populated from LITELLM_BRIDGE_* environment variables.
The gateway API key is intentionally read from the environment only, never
persisted into the data directory.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_DATA_DIR: Final[Path] = Path.home() / ".litellm" / "ide-bridge"
DEFAULT_LISTEN_HOST: Final[str] = "127.0.0.1"
DEFAULT_LISTEN_PORT: Final[int] = 443
DEFAULT_GATEWAY_URL: Final[str] = "http://127.0.0.1:4000"
DEFAULT_ROUTE_HOSTS: Final[tuple[str, ...]] = ("api.anthropic.com",)
DEFAULT_PASSTHROUGH_HOSTS: Final[tuple[str, ...]] = (
    "cloudcode-pa.googleapis.com",
    "daily-cloudcode-pa.googleapis.com",
)


class BridgeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path
    listen_host: str = DEFAULT_LISTEN_HOST
    listen_port: int = Field(default=DEFAULT_LISTEN_PORT, ge=1, le=65535)
    gateway_base_url: str = DEFAULT_GATEWAY_URL
    gateway_api_key: str | None = None
    route_hosts: tuple[str, ...] = DEFAULT_ROUTE_HOSTS
    passthrough_hosts: tuple[str, ...] = DEFAULT_PASSTHROUGH_HOSTS
    install_trust: bool = True

    @property
    def spoof_hosts(self) -> tuple[str, ...]:
        """Hosts that /etc/hosts points at the listener (routed + passthrough)."""
        return tuple(dict.fromkeys((*self.route_hosts, *self.passthrough_hosts)))

    @property
    def ca_cert_path(self) -> Path:
        return self.data_dir / "ca.crt"

    @property
    def ca_key_path(self) -> Path:
        return self.data_dir / "ca.key"

    @property
    def leaves_dir(self) -> Path:
        return self.data_dir / "leaves"

    @property
    def pidfile_path(self) -> Path:
        return self.data_dir / "bridge.pid"

    @property
    def capture_path(self) -> Path:
        return self.data_dir / "capture.jsonl"


def parse_host_list(raw: str) -> tuple[str, ...]:
    """Comma-separated env value into a de-duplicated host tuple."""
    return tuple(dict.fromkeys(part.strip().lower() for part in raw.split(",") if part.strip()))


def parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def bridge_config_from_env(env: Mapping[str, str]) -> BridgeConfig:
    def env_value(name: str, default: str) -> str:
        return env.get(name, default)

    port_raw: Final = env_value("LITELLM_BRIDGE_PORT", str(DEFAULT_LISTEN_PORT))
    try:
        port: Final[int] = int(port_raw)
    except ValueError as value_error:
        raise ValueError(f"LITELLM_BRIDGE_PORT is not a number: {port_raw!r}") from value_error

    return BridgeConfig(
        data_dir=Path(env_value("LITELLM_BRIDGE_DATA_DIR", str(DEFAULT_DATA_DIR))),
        listen_host=env_value("LITELLM_BRIDGE_HOST", DEFAULT_LISTEN_HOST),
        listen_port=port,
        gateway_base_url=env_value("LITELLM_BRIDGE_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/"),
        gateway_api_key=env.get("LITELLM_BRIDGE_API_KEY"),
        route_hosts=parse_host_list(env_value("LITELLM_BRIDGE_ROUTE_HOSTS", ",".join(DEFAULT_ROUTE_HOSTS))),
        passthrough_hosts=parse_host_list(
            env_value(
                "LITELLM_BRIDGE_PASSTHROUGH_HOSTS",
                ",".join(DEFAULT_PASSTHROUGH_HOSTS),
            )
        ),
        install_trust=parse_bool(env_value("LITELLM_BRIDGE_INSTALL_TRUST", "1")),
    )


def bridge_config() -> BridgeConfig:
    return bridge_config_from_env(os.environ)

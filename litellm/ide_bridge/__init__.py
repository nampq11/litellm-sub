"""IDE packet capture bridge for LiteLLM.

A local TLS-terminating proxy that captures what coding IDEs and CLI agents
send, routes the LLM portion through a LiteLLM gateway, and passes everything
else through untouched. Run with ``python -m litellm.ide_bridge``.
"""

from litellm.ide_bridge.capture import CapturedRequest, CaptureSink, read_captures
from litellm.ide_bridge.certs import (
    BridgeCertificates,
    CertificatePair,
    LeafPaths,
    generate_ca,
    load_ca_key,
    load_or_create_ca,
)
from litellm.ide_bridge.config import (
    BridgeConfig,
    bridge_config,
    bridge_config_from_env,
    parse_host_list,
)
from litellm.ide_bridge.forward import (
    GatewayRoute,
    OutboundRequest,
    Passthrough,
    UpstreamTarget,
    build_outbound,
    decide_upstream,
)
from litellm.ide_bridge.hosts import (
    apply_hosts_block,
    hosts_block,
    merge_hosts_block,
    remove_hosts_block,
    strip_hosts_block,
)
from litellm.ide_bridge.manager import (
    apply_system_changes,
    bridge_status,
    build_server,
    run_bridge,
    stop_bridge,
    teardown_system_changes,
)
from litellm.ide_bridge.server import BridgeServer, RequestHead, parse_request_head
from litellm.ide_bridge.trust_store import (
    install_commands,
    uninstall_commands,
)

__all__ = [
    "BridgeCertificates",
    "BridgeConfig",
    "BridgeServer",
    "CaptureSink",
    "CapturedRequest",
    "CertificatePair",
    "GatewayRoute",
    "LeafPaths",
    "OutboundRequest",
    "Passthrough",
    "RequestHead",
    "UpstreamTarget",
    "apply_hosts_block",
    "apply_system_changes",
    "bridge_config",
    "bridge_config_from_env",
    "bridge_status",
    "build_outbound",
    "build_server",
    "decide_upstream",
    "generate_ca",
    "hosts_block",
    "install_commands",
    "load_ca_key",
    "load_or_create_ca",
    "merge_hosts_block",
    "parse_host_list",
    "parse_request_head",
    "read_captures",
    "remove_hosts_block",
    "run_bridge",
    "stop_bridge",
    "strip_hosts_block",
    "teardown_system_changes",
    "uninstall_commands",
]

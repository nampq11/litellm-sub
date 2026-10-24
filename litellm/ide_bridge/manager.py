"""Start/stop lifecycle for the IDE bridge.

Setup order matters: the CA is trusted FIRST and the hosts block applied only
after trust succeeded. If trust installation fails, the bridge refuses to
start, because a hosts redirect without a trusted certificate would break the
IDE's traffic entirely instead of capturing it. Teardown is the mirror image
and is idempotent so a crashed bridge can always be cleaned by a re-run.
"""

import asyncio
import contextlib
import datetime
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

from litellm.ide_bridge.capture import CaptureSink
from litellm.ide_bridge.certs import (
    BridgeCertificates,
    CertificatePair,
    load_ca_key,
    load_or_create_ca,
)
from litellm.ide_bridge.config import BridgeConfig
from litellm.ide_bridge.forward import decide_upstream
from litellm.ide_bridge.hosts import (
    HOSTS_PATH,
    MARKER_TAG,
    apply_hosts_block,
    remove_hosts_block,
)
from litellm.ide_bridge.server import BridgeServer
from litellm.ide_bridge.trust_store import (
    TrustStoreError,
    current_platform,
    install_commands,
    run_commands,
    uninstall_commands,
)

OS_RELEASE_PATH: Final[Path] = Path("/etc/os-release")
PID_POLL_SECONDS: Final[float] = 0.2
STOP_TIMEOUT_SECONDS: Final[float] = 10.0
TERMINATE_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM)


def os_release_pretty_name(os_release_file: Path = OS_RELEASE_PATH) -> str:
    if not os_release_file.exists():
        return ""
    pretty_names: Final[tuple[str, ...]] = tuple(
        line.split("=", 1)[1].strip().strip('"')
        for line in os_release_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("PRETTY_NAME=")
    )
    return pretty_names[0] if pretty_names else ""


def read_pidfile(pidfile: Path) -> int | None:
    if not pidfile.exists():
        return None
    try:
        return int(pidfile.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def write_pidfile(pidfile: Path, pid: int) -> None:
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(f"{pid}\n", encoding="utf-8")


def pid_alive(pid: int, send_signal: Callable[[int, int], None] = os.kill) -> bool:
    try:
        send_signal(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def build_server(config: BridgeConfig) -> BridgeServer:
    ca: Final[CertificatePair] = load_or_create_ca(
        config.ca_cert_path, config.ca_key_path, datetime.datetime.now(datetime.UTC)
    )
    certificates: Final[BridgeCertificates] = BridgeCertificates(
        ca.cert_pem, load_ca_key(ca.key_pem), config.leaves_dir
    )
    return BridgeServer(
        config=config,
        certificates=certificates,
        capture_sink=CaptureSink(config.capture_path),
        decide_upstream=decide_upstream,
        client_factory=BridgeServer.default_client,
    )


def apply_system_changes(config: BridgeConfig, hosts_file: Path = HOSTS_PATH) -> None | str:
    """Trust the CA, then redirect the spoofed hosts. None means success."""
    if not config.install_trust:
        return None
    commands: Final = install_commands(current_platform(), os_release_pretty_name(), str(config.ca_cert_path))
    if isinstance(commands, TrustStoreError):
        return str(commands)
    install_error: Final = run_commands(commands)
    if install_error is not None:
        return f"could not install CA into OS trust store: {install_error}"
    apply_hosts_block(hosts_file, MARKER_TAG, config.listen_host, config.spoof_hosts)
    return None


def teardown_system_changes(config: BridgeConfig, hosts_file: Path = HOSTS_PATH) -> None:
    if config.install_trust:
        remove_hosts_block(hosts_file, MARKER_TAG)
        commands: Final = uninstall_commands(current_platform(), os_release_pretty_name())
        if not isinstance(commands, TrustStoreError):
            with contextlib.suppress(TrustStoreError):
                run_commands(commands)
    with contextlib.suppress(OSError):
        config.pidfile_path.unlink(missing_ok=True)


async def run_bridge(config: BridgeConfig) -> None:
    """Foreground daemon entry point. Ctrl-C / SIGTERM stops and cleans up."""
    server: Final = build_server(config)
    setup_error: Final = apply_system_changes(config)
    if setup_error is not None:
        print(f"bridge setup failed: {setup_error}", file=sys.stderr)
        raise SystemExit(1)
    write_pidfile(config.pidfile_path, os.getpid())
    print(
        f"ide bridge listening on https://{config.listen_host}:{config.listen_port} "
        f"(gateway {config.gateway_base_url}, capture {config.capture_path})",
        file=sys.stderr,
    )
    try:
        await _serve_until_stopped(server)
    finally:
        teardown_system_changes(config)


async def _serve_until_stopped(server: BridgeServer) -> None:
    stop_event: Final = asyncio.Event()
    loop: Final = asyncio.get_running_loop()
    for stop_signal in TERMINATE_SIGNALS:
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(stop_signal, stop_event.set)
    serve_task: Final = asyncio.ensure_future(server.serve_forever())
    stop_task: Final = asyncio.ensure_future(stop_event.wait())
    done, pending = await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    if serve_task in done:
        serve_task.result()


def stop_bridge(
    config: BridgeConfig,
    hosts_file: Path = HOSTS_PATH,
    send_signal: Callable[[int, int], None] = os.kill,
) -> str:
    pid: Final = read_pidfile(config.pidfile_path) or 0
    was_running: Final = pid > 0 and pid_alive(pid, send_signal)
    if was_running:
        try:
            send_signal(pid, signal.SIGTERM)
        except PermissionError:
            return f"cannot stop pid {pid}: permission denied"
        deadline: Final = time.monotonic() + STOP_TIMEOUT_SECONDS
        while time.monotonic() < deadline and pid_alive(pid, send_signal):
            time.sleep(PID_POLL_SECONDS)
    teardown_system_changes(config, hosts_file)
    if was_running:
        return f"stopped bridge (pid {pid})"
    return "bridge was not running; cleaned up leftover state"


def bridge_status(config: BridgeConfig, send_signal: Callable[[int, int], None] = os.kill) -> str:
    pid: Final = read_pidfile(config.pidfile_path) or 0
    if pid > 0 and pid_alive(pid, send_signal):
        return f"running (pid {pid}, listening on {config.listen_host}:{config.listen_port})"
    return "stopped"

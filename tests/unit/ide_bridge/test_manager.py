import signal
from pathlib import Path

from litellm.ide_bridge.config import BridgeConfig
from litellm.ide_bridge.manager import (
    apply_system_changes,
    bridge_status,
    os_release_pretty_name,
    pid_alive,
    read_pidfile,
    stop_bridge,
    teardown_system_changes,
    write_pidfile,
)

PRE_EXISTING_HOSTS: str = "127.0.0.1 localhost\n"


def _config(**overrides: object) -> BridgeConfig:
    values: dict[str, object] = {
        "data_dir": "/tmp/bridge",
        "route_hosts": ("api.anthropic.com",),
    }
    values.update(overrides)
    return BridgeConfig.model_validate(values)


class TestPidfile:
    def test_round_trip(self, tmp_path: Path):
        pidfile = tmp_path / "bridge.pid"

        write_pidfile(pidfile, 4242)

        assert read_pidfile(pidfile) == 4242

    def test_missing_file_and_garbage_content_read_as_none(self, tmp_path: Path):
        assert read_pidfile(tmp_path / "missing.pid") is None

        garbage = tmp_path / "garbage.pid"
        garbage.write_text("not a pid\n")
        assert read_pidfile(garbage) is None


def _alive_sender(pid: int, sig: int) -> None:
    return None


def _dead_sender(pid: int, sig: int) -> None:
    raise ProcessLookupError


class TestPidAlive:
    def test_signal_zero_success_means_alive(self):
        assert pid_alive(1, send_signal=_alive_sender)

    def test_process_lookup_error_means_dead(self):
        assert not pid_alive(1, send_signal=_dead_sender)

    def test_permission_error_means_alive_but_foreign(self):
        def foreign(pid: int, sig: int) -> None:
            raise PermissionError

        assert pid_alive(1, send_signal=foreign)


class TestOsReleasePrettyName:
    def test_reads_pretty_name_from_os_release(self, tmp_path: Path):
        os_release = tmp_path / "os-release"
        os_release.write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.1 LTS"\n')

        assert os_release_pretty_name(os_release) == "Ubuntu 24.04.1 LTS"

    def test_missing_file_is_an_empty_string(self, tmp_path: Path):
        assert os_release_pretty_name(tmp_path / "missing") == ""


class TestStopBridge:
    def test_dead_pid_still_cleans_up_leftover_state(self, tmp_path: Path):
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text(PRE_EXISTING_HOSTS)
        pidfile = tmp_path / "bridge.pid"
        pidfile.write_text("999999999\n")

        config = _config(data_dir=tmp_path)
        message = stop_bridge(
            config,
            hosts_file=hosts_file,
            send_signal=_dead_sender,
        )

        assert "not running" in message
        assert not pidfile.exists()

    def test_live_pid_is_terminated_then_cleaned_up(self, tmp_path: Path):
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text(PRE_EXISTING_HOSTS)
        pidfile = tmp_path / "bridge.pid"
        pidfile.write_text("4242\n")
        sent: list[tuple[int, int]] = []
        terminated: set[int] = set()

        def sender(pid: int, sig: int) -> None:
            sent.append((pid, sig))
            if sig == signal.SIGTERM:
                terminated.add(pid)
            elif pid in terminated:
                raise ProcessLookupError

        message = stop_bridge(config=_config(data_dir=tmp_path), hosts_file=hosts_file, send_signal=sender)

        assert (4242, signal.SIGTERM) in sent
        assert "stopped" in message
        assert not pidfile.exists()


class TestBridgeStatus:
    def test_pidfile_with_live_pid_reports_running(self, tmp_path: Path):
        pidfile = tmp_path / "bridge.pid"
        pidfile.write_text("4242\n")

        status = bridge_status(_config(data_dir=tmp_path), send_signal=_alive_sender)

        assert "running" in status
        assert "4242" in status

    def test_no_pidfile_reports_stopped(self, tmp_path: Path):
        assert bridge_status(_config(data_dir=tmp_path), send_signal=_dead_sender) == "stopped"


class TestApplySystemChanges:
    def test_no_trust_install_skips_hosts_and_trust_store(self, tmp_path: Path):
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text(PRE_EXISTING_HOSTS)

        result = apply_system_changes(_config(data_dir=tmp_path, install_trust=False), hosts_file=hosts_file)

        assert result is None
        assert hosts_file.read_text() == PRE_EXISTING_HOSTS

    def test_teardown_with_no_trust_install_leaves_hosts_untouched(self, tmp_path: Path):
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text(PRE_EXISTING_HOSTS)

        teardown_system_changes(_config(data_dir=tmp_path, install_trust=False), hosts_file=hosts_file)

        assert hosts_file.read_text() == PRE_EXISTING_HOSTS

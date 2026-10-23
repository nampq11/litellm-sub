import subprocess
from typing import Callable, Final

import pytest

from litellm.ide_bridge.trust_store import (
    TrustStoreError,
    detect_linux_family,
    install_commands,
    run_commands,
    uninstall_commands,
)

STAGED_CERT: Final[str] = "/var/lib/litellm-bridge/ca.crt"


class TestDetectLinuxFamily:
    @pytest.mark.parametrize(
        ("pretty_name", "family"),
        [
            ("Ubuntu 24.04.1 LTS", "debian"),
            ("Debian GNU/Linux 12", "debian"),
            ("Linux Mint 22", "debian"),
            ("Fedora Linux 41", "rhel"),
            ("Red Hat Enterprise Linux 9.4", "rhel"),
            ("Rocky Linux 9.4", "rhel"),
            ("Arch Linux", "arch"),
            ("Manjaro Linux", "arch"),
            ("openSUSE Tumbleweed", "suse"),
            ("Some Unknown Distro", "debian"),
        ],
    )
    def test_pretty_name_maps_to_a_trust_family(self, pretty_name: str, family: str):
        assert detect_linux_family(pretty_name) == family


class TestInstallCommands:
    def test_debian_copies_into_anchors_then_refreshes(self):
        commands = install_commands("linux", "Ubuntu 24.04.1 LTS", STAGED_CERT)

        assert commands == (
            ("/bin/cp", STAGED_CERT, "/usr/local/share/ca-certificates/litellm-ide-bridge-ca.crt"),
            ("/usr/sbin/update-ca-certificates",),
        )

    def test_fedora_uses_the_rhel_trust_path(self):
        commands = install_commands("linux", "Fedora Linux 41", STAGED_CERT)

        assert commands[0] == (
            "/bin/cp",
            STAGED_CERT,
            "/etc/pki/ca-trust/source/anchors/litellm-ide-bridge-ca.crt",
        )
        assert commands[1] == ("/usr/bin/update-ca-trust",)

    def test_macos_adds_a_trusted_root_to_the_system_keychain(self):
        commands = install_commands("darwin", "", STAGED_CERT)

        assert commands[0][0] == "/usr/bin/security"
        assert commands[0][1] == "add-trusted-cert"
        assert commands[0][-1] == STAGED_CERT

    def test_windows_uses_certutil(self):
        commands = install_commands("windows", "", STAGED_CERT)

        assert commands == (("certutil", "-addstore", "Root", STAGED_CERT),)

    def test_unknown_platform_returns_an_error_value(self):
        commands = install_commands("haiku", "", STAGED_CERT)

        assert isinstance(commands, TrustStoreError)


class TestUninstallCommands:
    def test_linux_removes_the_anchor_then_refreshes(self):
        commands = uninstall_commands("linux", "Ubuntu 24.04.1 LTS")

        assert commands == (
            ("/bin/rm", "-f", "/usr/local/share/ca-certificates/litellm-ide-bridge-ca.crt"),
            ("/usr/sbin/update-ca-certificates",),
        )

    def test_macos_and_windows_map_to_their_native_removals(self):
        darwin = uninstall_commands("darwin", "")
        windows = uninstall_commands("windows", "")

        assert darwin[0][1] == "delete-certificate"
        assert windows == (("certutil", "-delstore", "Root", "LiteLLM IDE Bridge CA"),)


class TestRunCommands:
    def test_all_successful_commands_return_none(self):
        commands = (("/bin/true",),)

        result = run_commands(commands, _fake_runner(0))

        assert result is None

    def test_a_failing_command_returns_an_error_value(self):
        commands = (("/bin/cp", "a", "b"),)

        result = run_commands(commands, _fake_runner(1, stderr="cp: cannot stat"))

        assert isinstance(result, TrustStoreError)
        assert "cp" in str(result)
        assert "cannot stat" in str(result)

    def test_a_missing_binary_returns_an_error_value(self):
        commands = (("/nonexistent/update-ca-certificates",),)

        def missing_binary(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(command[0])

        result = run_commands(commands, missing_binary)

        assert isinstance(result, TrustStoreError)


def _fake_runner(returncode: int, stderr: str = "") -> "Callable[..., subprocess.CompletedProcess[str]]":
    def run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=command, returncode=returncode, stdout="", stderr=stderr)

    return run

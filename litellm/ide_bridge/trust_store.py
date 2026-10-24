"""OS trust-store registration for the bridge CA.

Every platform keeps its system CA anchors in a different place and refreshes
them with a different command. This module builds argument tuples and runs
them; failures are returned as values so the bridge can degrade to a warning
instead of dying mid-setup.
"""

import platform
import subprocess
from collections.abc import Callable, Mapping
from typing import Final, TypeAlias

CA_INSTALL_NAME: Final[str] = "litellm-ide-bridge-ca.crt"
CA_COMMON_NAME: Final[str] = "LiteLLM IDE Bridge CA"
CA_COMMAND_TIMEOUT_SECONDS: Final[int] = 60

Command: TypeAlias = tuple[str, ...]

LINUX_TRUST: Final[Mapping[str, tuple[str, str]]] = {
    "debian": ("/usr/local/share/ca-certificates", "/usr/sbin/update-ca-certificates"),
    "rhel": ("/etc/pki/ca-trust/source/anchors", "/usr/bin/update-ca-trust"),
    "arch": ("/etc/ca-certificates/trust-source/anchors", "/usr/bin/update-ca-trust"),
    "suse": ("/etc/pki/trust/anchors", "/usr/sbin/update-ca-certificates"),
}


class TrustStoreError(Exception):
    pass


def detect_linux_family(os_release_pretty_name: str) -> str:
    """Map a PRETTY_NAME from /etc/os-release onto the distro families above."""
    pretty: Final = os_release_pretty_name.lower()
    if "ubuntu" in pretty or "debian" in pretty or "mint" in pretty:
        return "debian"
    if any(marker in pretty for marker in ("fedora", "red hat", "rhel", "centos", "rocky", "alma")):
        return "rhel"
    if "arch" in pretty or "manjaro" in pretty:
        return "arch"
    if "suse" in pretty or "opensuse" in pretty:
        return "suse"
    return "debian"


def install_commands(
    system: str, os_release_pretty_name: str, staged_cert_path: str
) -> tuple[Command, ...] | TrustStoreError:
    """Commands that make the OS trust the bridge CA. Arg arrays, never a shell."""
    if system == "linux":
        anchors_dir, refresh = LINUX_TRUST[detect_linux_family(os_release_pretty_name)]
        return (
            ("/bin/cp", staged_cert_path, f"{anchors_dir}/{CA_INSTALL_NAME}"),
            (refresh,),
        )
    if system == "darwin":
        return (
            (
                "/usr/bin/security",
                "add-trusted-cert",
                "-d",
                "-r",
                "trustRoot",
                "-k",
                "/Library/Keychains/System.keychain",
                staged_cert_path,
            ),
        )
    if system == "windows":
        return (("certutil", "-addstore", "Root", staged_cert_path),)
    return TrustStoreError(f"unsupported platform: {system}")


def uninstall_commands(system: str, os_release_pretty_name: str) -> tuple[Command, ...] | TrustStoreError:
    if system == "linux":
        anchors_dir, refresh = LINUX_TRUST[detect_linux_family(os_release_pretty_name)]
        return (
            ("/bin/rm", "-f", f"{anchors_dir}/{CA_INSTALL_NAME}"),
            (refresh,),
        )
    if system == "darwin":
        return (
            (
                "/usr/bin/security",
                "delete-certificate",
                "-c",
                CA_COMMON_NAME,
                "/Library/Keychains/System.keychain",
            ),
        )
    if system == "windows":
        return (("certutil", "-delstore", "Root", CA_COMMON_NAME),)
    return TrustStoreError(f"unsupported platform: {system}")


# Callable[..., CompletedProcess[str]]: same shape as subprocess.run
CommandRunner: TypeAlias = Callable[..., subprocess.CompletedProcess[str]]


def run_commands(commands: tuple[Command, ...], run: CommandRunner = subprocess.run) -> None | TrustStoreError:
    for command in commands:
        try:
            result = run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=CA_COMMAND_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as run_error:
            return TrustStoreError(f"could not run {' '.join(command)}: {run_error}")
        if result.returncode != 0:
            return TrustStoreError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr}")
    return None


def current_platform() -> str:
    system: Final = platform.system().lower()
    return "windows" if system.startswith("win") else system

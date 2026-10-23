"""/etc/hosts redirection for the bridge.

Pointing IDE traffic at the local listener means editing /etc/hosts, which the
file's owners also edit by hand. All changes are fenced between BEGIN/END
markers so applying twice is idempotent and removing restores the exact prior
content. Pure string helpers live at the top so tests can drive them without
root.
"""

from pathlib import Path
from typing import Final

HOSTS_PATH: Final[Path] = Path("/etc/hosts")
MARKER_TAG: Final[str] = "litellm-ide-bridge"


def begin_marker(marker_tag: str) -> str:
    return f"# BEGIN {marker_tag}"


def end_marker(marker_tag: str) -> str:
    return f"# END {marker_tag}"


def hosts_block(marker_tag: str, listen_ip: str, hosts: tuple[str, ...]) -> str:
    lines: Final = (begin_marker(marker_tag), *(f"{listen_ip} {host}" for host in hosts), end_marker(marker_tag))
    return "\n".join(lines)


def merge_hosts_block(current_text: str, block: str, marker_tag: str) -> str:
    """Insert or replace the fenced block, preserving everything else verbatim."""
    stripped: Final = strip_hosts_block(current_text, marker_tag)
    return f"{stripped.rstrip()}\n{block}\n"


def strip_hosts_block(current_text: str, marker_tag: str) -> str:
    begin: Final = begin_marker(marker_tag)
    end: Final = end_marker(marker_tag)
    begin_index: Final = current_text.find(begin)
    if begin_index == -1:
        return current_text
    end_index: Final = current_text.find(end, begin_index)
    if end_index == -1:
        return current_text[:begin_index]
    tail: Final[str] = current_text[end_index + len(end) :]
    return current_text[:begin_index] + tail.lstrip("\n")


def apply_hosts_block(hosts_file: Path, marker_tag: str, listen_ip: str, hosts: tuple[str, ...]) -> None:
    current: Final[str] = hosts_file.read_text()
    hosts_file.write_text(merge_hosts_block(current, hosts_block(marker_tag, listen_ip, hosts), marker_tag))


def remove_hosts_block(hosts_file: Path, marker_tag: str) -> None:
    if not hosts_file.exists():
        return
    current: Final[str] = hosts_file.read_text()
    stripped: Final[str] = strip_hosts_block(current, marker_tag)
    if stripped != current:
        hosts_file.write_text(stripped)

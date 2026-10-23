import re
from typing import Final

from litellm.litellm_core_utils.token_saver.constants import STATUS_MAX_FILES, STATUS_MAX_UNTRACKED

FILTER_NAME: Final[str] = "git-status"
_RE_BRANCH: Final = re.compile(r"^On branch (\S+)")
_RE_LONG: Final = re.compile(r"^\s*(modified|new file|deleted|renamed|both modified):\s+(.+)$")


class _StatusState:
    __slots__ = ("branch", "conflicts", "modified_files", "staged_files", "untracked_files")

    def __init__(self) -> None:
        self.branch: str = ""
        self.staged_files: Final[list[str]] = []  # mutable-ok: builder for staged list
        self.modified_files: Final[list[str]] = []  # mutable-ok: builder for modified list
        self.untracked_files: Final[list[str]] = []  # mutable-ok: builder for untracked list
        self.conflicts: int = 0
    def parse_porcelain(self, raw: str) -> bool:
        if len(raw) < 3 or raw[0] not in " MADRCU?!" or raw[1] not in " MADRCU?!" or raw[2] != " ":
            return False
        x: Final = raw[0]
        y: Final = raw[1]
        file_path: Final = raw[3:].strip()
        if x == "?" and y == "?":
            self.untracked_files.append(file_path)
            return True
        if x in "MADRC":
            self.staged_files.append(file_path)
        elif x == "U":
            self.conflicts += 1
        if y in ("M", "D"):
            self.modified_files.append(file_path)
        return True

    def parse_long_form(self, raw: str) -> bool:
        long_match: Final = _RE_LONG.match(raw)
        if long_match is None:
            return False
        kind: Final = long_match.group(1)
        path: Final = long_match.group(2).strip()
        if kind == "both modified":
            self.conflicts += 1
        elif kind in ("modified", "deleted"):
            self.modified_files.append(path)
        elif kind in ("new file", "renamed"):
            self.staged_files.append(path)
        return True

def _append_file_section(out: list[str], prefix: str, files: list[str], max_show: int) -> None:  # mutable-ok: builder append
    if not files:
        return
    out.append(f"{prefix}: {len(files)} files")
    out.extend(f"   {f}" for f in files[:max_show])
    if len(files) > max_show:
        out.append(f"   ... +{len(files) - max_show} more")


def _format_status(state: _StatusState) -> str:
    out: Final[list[str]] = []  # mutable-ok: builder for status lines
    if state.branch:
        out.append(f"* {state.branch}")

    _append_file_section(out, "+ Staged", state.staged_files, STATUS_MAX_FILES)
    _append_file_section(out, "~ Modified", state.modified_files, STATUS_MAX_FILES)
    _append_file_section(out, "? Untracked", state.untracked_files, STATUS_MAX_UNTRACKED)

    if state.conflicts > 0:
        out.append(f"conflicts: {state.conflicts} files")

    if not state.staged_files and not state.modified_files and not state.untracked_files and state.conflicts == 0:
        out.append("clean - nothing to commit")

    return "\n".join(out)


def filter_git_status(input_text: str) -> str:
    lines: Final = input_text.splitlines()
    if not lines or (len(lines) == 1 and not lines[0].strip()):
        return "Clean working tree"

    state: Final = _StatusState()
    for raw in lines:
        if not raw.strip():
            continue
        m = _RE_BRANCH.match(raw)
        if m is not None:
            state.branch = m.group(1)
            continue
        if raw.startswith("##"):
            state.branch = raw[2:].strip()
            continue
        if state.parse_porcelain(raw):
            continue
        state.parse_long_form(raw)

    return _format_status(state)

import re
from collections.abc import Callable, Sequence
from typing import Final

from litellm.litellm_core_utils.token_saver.constants import (
    DETECT_WINDOW,
    READ_NUMBERED_MIN_HIT_RATIO,
    SMART_TRUNCATE_MIN_LINES,
)
from litellm.litellm_core_utils.token_saver.filters.build_output import filter_build_output
from litellm.litellm_core_utils.token_saver.filters.dedup_log import filter_dedup_log
from litellm.litellm_core_utils.token_saver.filters.find_filter import filter_find
from litellm.litellm_core_utils.token_saver.filters.git_diff import filter_git_diff
from litellm.litellm_core_utils.token_saver.filters.git_log import filter_git_log
from litellm.litellm_core_utils.token_saver.filters.git_status import filter_git_status
from litellm.litellm_core_utils.token_saver.filters.grep_filter import filter_grep
from litellm.litellm_core_utils.token_saver.filters.ls_filter import filter_ls
from litellm.litellm_core_utils.token_saver.filters.read_numbered import (
    READ_NUMBERED_LINE_RE,
    filter_read_numbered,
)
from litellm.litellm_core_utils.token_saver.filters.smart_truncate import filter_smart_truncate
from litellm.litellm_core_utils.token_saver.filters.tree_filter import filter_tree

_RE_GIT_DIFF: Final = re.compile(r"^diff --git ", re.MULTILINE)
_RE_GIT_DIFF_HUNK: Final = re.compile(r"^@@ ", re.MULTILINE)
_RE_GIT_STATUS: Final = re.compile(
    r"^On branch |^nothing to commit|^Changes (not |to be )|^Untracked files:",
    re.MULTILINE,
)
_RE_GIT_LOG: Final = re.compile(r"^[*|/\\ ]*commit [0-9a-f]{7,40}", re.MULTILINE | re.IGNORECASE)
_RE_PORCELAIN: Final = re.compile(r"^[ MADRCU?!]{2} \S", re.MULTILINE)
_RE_BUILD_OUTPUT: Final = re.compile(
    r"^(npm (warn|error|ERR!)|yarn (warn|error)|\s*Compiling\s+\S+|\s*Downloading\s+\S+|added \d+ package|\[ERROR\]|BUILD (SUCCESS|FAILED)|\s*Finished\s+|Successfully (installed|built)|ERROR:)",
    re.MULTILINE | re.IGNORECASE,
)
_RE_TREE_GLYPH: Final = re.compile(r"[├└]──|│  ")
_RE_LS_ROW: Final = re.compile(r"^[-dlbcps][rwx-]{9}", re.MULTILINE)
_RE_LS_TOTAL: Final = re.compile(r"^total \d+$", re.MULTILINE)


def _is_grep_line(line: str) -> bool:
    first: Final = line.find(":")
    if first == -1:
        return False
    second: Final = line.find(":", first + 1)
    if second == -1:
        return False
    return line[first + 1 : second].isdigit()


def _is_path_like(line: str) -> bool:
    trimmed: Final = line.strip()
    if not trimmed:
        return False
    if len(trimmed) >= 3 and trimmed[1] == ":" and trimmed[2] in ("\\", "/"):
        return True
    if ":" in trimmed:
        return False
    return trimmed.startswith((".", "/")) or "/" in trimmed or "\\" in trimmed


def _is_mostly_porcelain(head: str) -> bool:
    lines: Final = [line.strip() for line in head.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    hits: Final = len(_RE_PORCELAIN.findall(head))
    return (hits / len(lines)) >= 0.6


def _is_line_numbered(lines: Sequence[str]) -> bool:
    non_empty: Final[list[str]] = [line for line in lines[:100] if line.strip()]  # mutable-ok: sample lines
    if len(non_empty) < 5:
        return False
    hits: Final = sum(1 for line in non_empty if READ_NUMBERED_LINE_RE.search(line) is not None)
    return (hits / len(non_empty)) >= READ_NUMBERED_MIN_HIT_RATIO


def _detect_git_and_build(head: str) -> tuple[Callable[[str], str], str] | None:
    if _RE_GIT_LOG.search(head):
        return filter_git_log, "git-log"
    if _RE_GIT_DIFF.search(head) or _RE_GIT_DIFF_HUNK.search(head):
        return filter_git_diff, "git-diff"
    if _RE_GIT_STATUS.search(head):
        return filter_git_status, "git-status"
    if _RE_BUILD_OUTPUT.search(head):
        return filter_build_output, "build-output"
    if _is_mostly_porcelain(head):
        return filter_git_status, "git-status"
    return None


def _detect_structure(head: str, non_empty: Sequence[str]) -> tuple[Callable[[str], str], str] | None:
    first5: Final = non_empty[:5]
    if any(_is_grep_line(line) for line in first5):
        return filter_grep, "grep"

    if len(non_empty) >= 3 and all(_is_path_like(line) for line in non_empty):
        return filter_find, "find"

    if _RE_TREE_GLYPH.search(head):
        return filter_tree, "tree"

    if _RE_LS_TOTAL.search(head) or len(_RE_LS_ROW.findall(head)) >= 3:
        return filter_ls, "ls"

    return None


def auto_detect_filter(text: str) -> tuple[Callable[[str], str], str] | None:
    head: Final = text[:DETECT_WINDOW] if len(text) > DETECT_WINDOW else text

    git_or_build: Final = _detect_git_and_build(head)
    if git_or_build is not None:
        return git_or_build

    lines: Final = tuple(head.splitlines())
    non_empty: Final[tuple[str, ...]] = tuple(line for line in lines if line.strip())

    structure: Final = _detect_structure(head, non_empty)
    if structure is not None:
        return structure

    all_lines: Final = tuple(text.splitlines())
    if len(all_lines) >= SMART_TRUNCATE_MIN_LINES and _is_line_numbered(all_lines):
        return filter_read_numbered, "read-numbered"

    if len(non_empty) >= 5 and len(all_lines) >= 10:
        return filter_dedup_log, "dedup-log"

    if len(all_lines) >= SMART_TRUNCATE_MIN_LINES:
        return filter_smart_truncate, "smart-truncate"

    return None

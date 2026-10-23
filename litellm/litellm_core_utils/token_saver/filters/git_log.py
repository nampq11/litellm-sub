import re
from typing import Final

from litellm.litellm_core_utils.token_saver.constants import GIT_LOG_MAX_LINES

FILTER_NAME: Final[str] = "git-log"
_RE_COMMIT: Final = re.compile(r"^[*|/\\ ]*commit [0-9a-f]{7,40}", re.IGNORECASE)
_RE_AUTHOR_DATE: Final = re.compile(r"^[*|/\\ ]*(Author|Date):", re.IGNORECASE)
_RE_SUBJECT: Final = re.compile(r"^[*|/\\ ]*    \S")
_RE_STAT: Final = re.compile(r"^\d+ file\w* changed")


class _LogBuilder:
    __slots__ = ("in_commit", "out", "subject_seen")

    def __init__(self) -> None:
        self.out: Final[list[str]] = []  # mutable-ok: builder for git log output
        self.in_commit: bool = False
        self.subject_seen: bool = False

    def on_commit_line(self, line: str, trimmed: str) -> None:
        if _RE_AUTHOR_DATE.search(trimmed):
            self.out.append(trimmed)
            return
        if not trimmed:
            return
        if not self.subject_seen and _RE_SUBJECT.search(line):
            self.out.append(f"  Subject: {trimmed}")
            self.subject_seen = True
            return
        if _RE_STAT.search(trimmed):
            self.out.append(f"  {trimmed}")
            return
        if line.startswith("diff --git "):
            self.out.append("  ... diff body omitted")

    def process_line(self, line: str) -> None:
        trimmed: Final = line.strip()
        if _RE_COMMIT.search(trimmed):
            self.in_commit = True
            self.subject_seen = False
            self.out.append(line.rstrip())
            return

        if self.in_commit:
            self.on_commit_line(line, trimmed)
            return

        if trimmed:
            self.out.append(trimmed)


def filter_git_log(text: str, max_lines: int = GIT_LOG_MAX_LINES) -> str:
    if not text:
        return ""

    lines: Final = text.splitlines()
    builder: Final = _LogBuilder()

    for line in lines:
        if len(builder.out) >= max_lines:
            builder.out.append(f"... (truncated at {max_lines} lines)")
            break
        builder.process_line(line)

    return "\n".join(builder.out)

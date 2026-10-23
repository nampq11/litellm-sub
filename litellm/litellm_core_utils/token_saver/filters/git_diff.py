from typing import Final

from litellm.litellm_core_utils.token_saver.constants import GIT_DIFF_HUNK_MAX_LINES

FILTER_NAME: Final[str] = "git-diff"


class _DiffBuilder:
    __slots__ = (
        "added",
        "current_file",
        "hunk_shown",
        "hunk_skipped",
        "in_hunk",
        "removed",
        "result",
        "was_truncated",
    )

    def __init__(self) -> None:
        self.result: Final[list[str]] = []  # mutable-ok: builder for diff compression
        self.current_file: str = ""
        self.added: int = 0
        self.removed: int = 0
        self.in_hunk: bool = False
        self.hunk_shown: int = 0
        self.hunk_skipped: int = 0
        self.was_truncated: bool = False

    def flush_skipped(self) -> None:
        if self.hunk_skipped > 0:
            self.result.append(f"  ... ({self.hunk_skipped} lines truncated)")
            self.was_truncated = True
            self.hunk_skipped = 0

    def flush_file_summary(self) -> None:
        if self.current_file and (self.added > 0 or self.removed > 0):
            self.result.append(f"  +{self.added} -{self.removed}")

    def on_diff_header(self, line: str) -> None:
        self.flush_skipped()
        self.flush_file_summary()
        parts: Final = line.split(" b/")
        self.current_file = parts[1] if len(parts) > 1 else "unknown"
        self.result.append(f"\n{self.current_file}")
        self.added = 0
        self.removed = 0
        self.in_hunk = False
        self.hunk_shown = 0

    def on_hunk_header(self, line: str) -> None:
        self.flush_skipped()
        self.in_hunk = True
        self.hunk_shown = 0
        self.result.append(f"  {line}")

    def on_hunk_line(self, line: str) -> None:
        if line.startswith("+") and not line.startswith("+++"):
            self.added += 1
            if self.hunk_shown < GIT_DIFF_HUNK_MAX_LINES:
                self.result.append(f"  {line}")
                self.hunk_shown += 1
            else:
                self.hunk_skipped += 1
        elif line.startswith("-") and not line.startswith("---"):
            self.removed += 1
            if self.hunk_shown < GIT_DIFF_HUNK_MAX_LINES:
                self.result.append(f"  {line}")
                self.hunk_shown += 1
            else:
                self.hunk_skipped += 1
        elif self.hunk_shown < GIT_DIFF_HUNK_MAX_LINES and not line.startswith("\\"):
            if self.hunk_shown > 0:
                self.result.append(f"  {line}")
                self.hunk_shown += 1

    def build(self) -> str:
        self.flush_skipped()
        self.flush_file_summary()
        if self.was_truncated:
            self.result.append("[full diff: rtk git diff --no-compact]")
        return "\n".join(self.result).strip()


def filter_git_diff(diff: str, max_lines: int = 500) -> str:
    builder: Final = _DiffBuilder()
    lines: Final = diff.splitlines()

    for line in lines:
        if line.startswith("diff --git"):
            builder.on_diff_header(line)
        elif line.startswith("@@"):
            builder.on_hunk_header(line)
        elif builder.in_hunk:
            builder.on_hunk_line(line)

        if len(builder.result) >= max_lines:
            builder.result.append("\n... (more changes truncated)")
            builder.was_truncated = True
            break

    return builder.build()

from typing import Final

from litellm.litellm_core_utils.token_saver.constants import DEDUP_LINE_MAX

FILTER_NAME: Final[str] = "dedup-log"


def filter_dedup_log(input_text: str) -> str:
    lines: Final = input_text.splitlines()
    out: Final[list[str]] = []  # mutable-ok: builder for deduped lines
    prev: str | None = None  # rebind-ok: track previous line
    run_count: int = 0  # rebind-ok: track run count
    blank_streak: int = 0  # rebind-ok: track blank lines

    def _flush_run() -> None:
        if prev is not None and run_count > 1:
            out.append(f"  ... ({run_count - 1} duplicate lines)")

    for line in lines:
        if not line.strip():
            if blank_streak < 1:
                out.append(line)
            blank_streak += 1
            _flush_run()
            prev = None
            run_count = 0
            continue

        blank_streak = 0
        if line == prev:
            run_count += 1
            continue

        _flush_run()
        out.append(line)
        prev = line
        run_count = 1

        if len(out) >= DEDUP_LINE_MAX:
            out.append(f"... (truncated at {DEDUP_LINE_MAX} lines)")
            return "\n".join(out)

    _flush_run()
    return "\n".join(out)

import re
from typing import Final

FILTER_NAME: Final[str] = "build-output"
_RE_ERR: Final = re.compile(
    r"^(npm (ERR!|error)|yarn error|\[ERROR\]|BUILD FAILED|ERROR:|error(\[|:)|error -->)",
    re.IGNORECASE,
)
_RE_WARN: Final = re.compile(r"^(npm warn|yarn warn|\[WARNING\]|warning(\[|:)|warning -->)", re.IGNORECASE)
_RE_PROGRESS: Final = re.compile(r"^\s*(Compiling|Downloading|Fetch|added \d+ package|Finished|Installing)", re.IGNORECASE)


def _classify_line(trimmed: str) -> str:
    if _RE_ERR.search(trimmed):
        return "error"
    if _RE_WARN.search(trimmed):
        return "warning"
    low: Final = trimmed.lower()
    if "failed" in low or "error" in low:
        return "error"
    if _RE_PROGRESS.search(trimmed):
        return "progress"
    if "success" in low or "finished" in low or "summary" in low:
        return "summary"
    return "ignore"


def _format_build_output(errors: list[str], warnings: list[str], summaries: list[str]) -> str:  # mutable-ok: list format input
    out_parts: Final[list[str]] = []  # mutable-ok: builder for filtered output
    if errors:
        out_parts.append(f"Errors ({len(errors)}):")
        for err in errors[:50]:
            out_parts.append(f"  {err}")
        if len(errors) > 50:
            out_parts.append(f"  ... +{len(errors) - 50} more errors")

    if warnings:
        out_parts.append(f"Warnings ({len(warnings)}):")
        for warn in warnings[:20]:
            out_parts.append(f"  {warn}")
        if len(warnings) > 20:
            out_parts.append(f"  ... +{len(warnings) - 20} more warnings")

    if summaries:
        out_parts.append("Summary:")
        for s in summaries[:5]:
            out_parts.append(f"  {s}")

    return "\n".join(out_parts)


def filter_build_output(input_text: str) -> str:
    lines: Final = input_text.splitlines()
    if not lines:
        return input_text

    errors: Final[list[str]] = []  # mutable-ok: builder for errors
    warnings: Final[list[str]] = []  # mutable-ok: builder for warnings
    summaries: Final[list[str]] = []  # mutable-ok: builder for summary lines

    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            continue
        kind = _classify_line(trimmed)
        if kind == "error":
            errors.append(line)
        elif kind == "warning":
            warnings.append(line)
        elif kind == "summary":
            summaries.append(line)

    if not errors and not warnings:
        return input_text

    return _format_build_output(errors, warnings, summaries)

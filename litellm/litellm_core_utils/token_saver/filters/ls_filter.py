import re
from typing import Final, NamedTuple

from litellm.litellm_core_utils.token_saver.constants import LS_EXT_SUMMARY_TOP, LS_NOISE_DIRS

FILTER_NAME: Final[str] = "ls"
_RE_LS_DATE: Final = re.compile(
    r"\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+(\d{4}|\d{2}:\d{2})\s+"
)


class _ParsedLs(NamedTuple):
    name: str
    file_type: str
    size: int


def _human_size(bytes_val: int) -> str:
    if bytes_val >= 1_048_576:
        return f"{bytes_val / 1_048_576:.1f}M"
    if bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f}K"
    return f"{bytes_val}B"


def _parse_ls_line(line: str) -> _ParsedLs | None:
    if line.startswith("total ") or not line.strip():
        return None
    m: Final = _RE_LS_DATE.search(line)
    if m is None:
        return None
    name: Final = line[m.end() :].strip()
    if not name or name in (".", "..") or name in LS_NOISE_DIRS:
        return None

    before_date: Final = line[: m.start()].split()
    if len(before_date) < 4:
        return None
    file_type: Final = before_date[0][0]

    size: int = 0  # rebind-ok: rightmost size parsing
    for part in reversed(before_date):
        if part.isdigit():
            size = int(part)
            break
    return _ParsedLs(name=name, file_type=file_type, size=size)


def _format_ls_summary(dirs: list[str], files: list[tuple[str, str]], by_ext: dict[str, int]) -> str:  # mutable-ok: format input
    out_parts: Final[list[str]] = []  # mutable-ok: builder
    for d in sorted(dirs):
        out_parts.append(f"{d}/")
    for name, size_str in sorted(files):
        out_parts.append(f"{name}  {size_str}")

    summary: Final = f"\nSummary: {len(files)} files, {len(dirs)} dirs"
    if by_ext:
        sorted_exts: Final = sorted(by_ext.items(), key=lambda x: x[1], reverse=True)
        top_exts: Final = [f"{c} {e}" for e, c in sorted_exts[:LS_EXT_SUMMARY_TOP]]
        ext_summary: Final = f"{summary} ({', '.join(top_exts)})"
        out_parts.append(ext_summary)
    else:
        out_parts.append(summary)

    return "\n".join(out_parts)


def filter_ls(input_text: str) -> str:
    lines: Final = input_text.splitlines()
    dirs: Final[list[str]] = []  # mutable-ok: builder for dir list
    files: Final[list[tuple[str, str]]] = []  # mutable-ok: builder for file list
    by_ext: Final[dict[str, int]] = {}  # mutable-ok: builder for ext count

    for line in lines:
        parsed = _parse_ls_line(line)
        if parsed is None:
            continue

        if parsed.file_type == "d":
            dirs.append(parsed.name)
        elif parsed.file_type in ("-", "l"):
            dot_idx = parsed.name.rfind(".")
            ext = parsed.name[dot_idx:] if dot_idx > 0 else "no ext"
            by_ext[ext] = by_ext.get(ext, 0) + 1
            files.append((parsed.name, _human_size(parsed.size)))

    if not dirs and not files:
        return input_text

    return _format_ls_summary(dirs, files, by_ext)

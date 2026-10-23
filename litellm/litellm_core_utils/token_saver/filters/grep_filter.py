from typing import Final

from litellm.litellm_core_utils.token_saver.constants import GREP_PER_FILE_MAX

FILTER_NAME: Final[str] = "grep"


def filter_grep(input_text: str) -> str:
    lines: Final = input_text.splitlines()
    by_file: Final[dict[str, list[tuple[str, str]]]] = {}  # mutable-ok: builder for grep matches
    total: int = 0  # rebind-ok: count matches

    for line in lines:
        first = line.find(":")
        if first == -1:
            continue
        second = line.find(":", first + 1)
        if second == -1:
            continue

        file_path = line[:first]
        line_num = line[first + 1 : second]
        content = line[second + 1 :]
        if not line_num.isdigit():
            continue

        total += 1
        if file_path not in by_file:
            by_file[file_path] = []
        by_file[file_path].append((line_num, content))

    if total == 0:
        return input_text

    sorted_files: Final = sorted(by_file.keys())
    out_parts: Final[list[str]] = [f"{total} matches in {len(sorted_files)}F:\n"]  # mutable-ok: builder
    for file_key in sorted_files:
        matches = by_file[file_key]
        out_parts.append(f"[file] {file_key} ({len(matches)}):")
        show = matches[:GREP_PER_FILE_MAX]
        for l_num, cont in show:
            out_parts.append(f"  {l_num.rjust(4)}: {cont.strip()}")
        if len(matches) > GREP_PER_FILE_MAX:
            out_parts.append(f"  +{len(matches) - GREP_PER_FILE_MAX}")
        out_parts.append("")

    return "\n".join(out_parts).strip()

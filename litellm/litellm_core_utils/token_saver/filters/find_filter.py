from typing import Final

from litellm.litellm_core_utils.token_saver.constants import FIND_PER_DIR_MAX, FIND_TOTAL_DIR_MAX

FILTER_NAME: Final[str] = "find"


def filter_find(input_text: str) -> str:
    lines: Final = [line.strip() for line in input_text.splitlines() if line.strip()]
    if not lines:
        return input_text

    by_dir: Final[dict[str, list[str]]] = {}  # mutable-ok: builder for dir grouping
    for path in lines:
        last_sep = max(path.rfind("/"), path.rfind("\\"))
        dir_name = "." if last_sep == -1 else (path[:last_sep] or "/")
        base_name = path if last_sep == -1 else path[last_sep + 1 :]
        if dir_name not in by_dir:
            by_dir[dir_name] = []
        by_dir[dir_name].append(base_name)

    sorted_dirs: Final = sorted(by_dir.keys())
    out_parts: Final[list[str]] = [f"{len(lines)} files in {len(sorted_dirs)} dirs:\n"]  # mutable-ok: builder
    show_dirs: Final = sorted_dirs[:FIND_TOTAL_DIR_MAX]
    for d in show_dirs:
        files = by_dir[d]
        dir_label = d.replace("\\", "/")
        out_parts.append(f"{dir_label}/  ({len(files)})")
        for f in files[:FIND_PER_DIR_MAX]:
            out_parts.append(f"  {f}")
        if len(files) > FIND_PER_DIR_MAX:
            out_parts.append(f"  +{len(files) - FIND_PER_DIR_MAX}")

    if len(sorted_dirs) > FIND_TOTAL_DIR_MAX:
        out_parts.append(f"\n+{len(sorted_dirs) - FIND_TOTAL_DIR_MAX} more dirs")

    return "\n".join(out_parts)

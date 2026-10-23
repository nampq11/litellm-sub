from typing import Final

from litellm.litellm_core_utils.token_saver.constants import TREE_MAX_LINES

FILTER_NAME: Final[str] = "tree"


def filter_tree(input_text: str) -> str:
    lines: Final = input_text.splitlines()
    if not lines:
        return input_text

    filtered: Final[list[str]] = []  # mutable-ok: builder for tree lines
    for line in lines:
        if "director" in line and "file" in line:
            continue
        if not line.strip() and not filtered:
            continue
        filtered.append(line)

    while filtered and not filtered[-1].strip():
        filtered.pop()

    if len(filtered) > TREE_MAX_LINES:
        cut: Final = len(filtered) - TREE_MAX_LINES
        return "\n".join(filtered[:TREE_MAX_LINES]) + f"\n... +{cut} more lines"

    return "\n".join(filtered)

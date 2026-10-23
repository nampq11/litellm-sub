import re
from typing import Final

from litellm.litellm_core_utils.token_saver.constants import (
    SMART_TRUNCATE_HEAD,
    SMART_TRUNCATE_MIN_LINES,
    SMART_TRUNCATE_TAIL,
)

FILTER_NAME: Final[str] = "read-numbered"
READ_NUMBERED_LINE_RE: Final = re.compile(r"^\s*\d+[:|]")


def filter_read_numbered(input_text: str) -> str:
    lines: Final = input_text.splitlines()
    if len(lines) < SMART_TRUNCATE_MIN_LINES:
        return input_text

    head: Final = lines[:SMART_TRUNCATE_HEAD]
    tail: Final = lines[len(lines) - SMART_TRUNCATE_TAIL :]
    cut: Final = len(lines) - len(head) - len(tail)

    return "\n".join((*head, f"... +{cut} lines truncated (file continues)", *tail))

from collections.abc import Callable
from typing import Final

from .build_output import filter_build_output
from .dedup_log import filter_dedup_log
from .find_filter import filter_find
from .git_diff import filter_git_diff
from .git_log import filter_git_log
from .git_status import filter_git_status
from .grep_filter import filter_grep
from .ls_filter import filter_ls
from .read_numbered import filter_read_numbered
from .smart_truncate import filter_smart_truncate
from .tree_filter import filter_tree

FilterFn: Final = Callable[[str], str]

__all__: Final[tuple[str, ...]] = (
    "FilterFn",
    "filter_build_output",
    "filter_dedup_log",
    "filter_find",
    "filter_git_diff",
    "filter_git_log",
    "filter_git_status",
    "filter_grep",
    "filter_ls",
    "filter_read_numbered",
    "filter_smart_truncate",
    "filter_tree",
)

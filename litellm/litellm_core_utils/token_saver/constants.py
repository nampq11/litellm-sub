from typing import Final

RAW_CAP: Final[int] = 10 * 1024 * 1024
MIN_COMPRESS_SIZE: Final[int] = 500
DETECT_WINDOW: Final[int] = 1024
GIT_DIFF_HUNK_MAX_LINES: Final[int] = 100
GIT_LOG_MAX_LINES: Final[int] = 200
DEDUP_LINE_MAX: Final[int] = 2000

GREP_PER_FILE_MAX: Final[int] = 10
FIND_PER_DIR_MAX: Final[int] = 10
FIND_TOTAL_DIR_MAX: Final[int] = 20

STATUS_MAX_FILES: Final[int] = 10
STATUS_MAX_UNTRACKED: Final[int] = 10

TREE_MAX_LINES: Final[int] = 200

SEARCH_LIST_PER_DIR_MAX: Final[int] = 10
SEARCH_LIST_TOTAL_DIR_MAX: Final[int] = 20

SMART_TRUNCATE_HEAD: Final[int] = 120
SMART_TRUNCATE_TAIL: Final[int] = 60
SMART_TRUNCATE_MIN_LINES: Final[int] = 250

READ_NUMBERED_MIN_HIT_RATIO: Final[float] = 0.7

LS_EXT_SUMMARY_TOP: Final[int] = 5
LS_NOISE_DIRS: Final[tuple[str, ...]] = (
    "node_modules",
    ".git",
    "target",
    "__pycache__",
    ".next",
    "dist",
    "build",
    ".cache",
    ".turbo",
    ".vercel",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "coverage",
    ".nyc_output",
    ".DS_Store",
    "Thumbs.db",
    ".idea",
    ".vscode",
    ".vs",
    "*.egg-info",
    ".eggs",
)

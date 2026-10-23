from litellm.litellm_core_utils.token_saver.filters.build_output import filter_build_output
from litellm.litellm_core_utils.token_saver.filters.dedup_log import filter_dedup_log
from litellm.litellm_core_utils.token_saver.filters.find_filter import filter_find
from litellm.litellm_core_utils.token_saver.filters.git_diff import filter_git_diff
from litellm.litellm_core_utils.token_saver.filters.git_log import filter_git_log
from litellm.litellm_core_utils.token_saver.filters.git_status import filter_git_status
from litellm.litellm_core_utils.token_saver.filters.grep_filter import filter_grep
from litellm.litellm_core_utils.token_saver.filters.ls_filter import filter_ls
from litellm.litellm_core_utils.token_saver.filters.read_numbered import filter_read_numbered
from litellm.litellm_core_utils.token_saver.filters.smart_truncate import filter_smart_truncate
from litellm.litellm_core_utils.token_saver.filters.tree_filter import filter_tree


def test_git_diff_filter_compacts_hunks():
    raw_diff = (
        "diff --git a/src/main.py b/src/main.py\n"
        "index 1234567..89abcdef 100644\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,5 +1,6 @@\n"
        " def main():\n"
        "-    print('hello')\n"
        "+    print('hello world')\n"
        "+    return 0\n"
    )
    result = filter_git_diff(raw_diff)
    assert "src/main.py" in result
    assert "+2 -1" in result
    assert "print('hello world')" in result


def test_git_diff_filter_truncates_large_hunk():
    many_added = "\n".join(f"+line_{i} = {i}" for i in range(150))
    raw_diff = (
        "diff --git a/big_file.py b/big_file.py\n"
        "@@ -1,1 +1,150 @@\n"
        f"{many_added}\n"
    )
    result = filter_git_diff(raw_diff)
    assert "... (50 lines truncated)" in result
    assert "[full diff: rtk git diff --no-compact]" in result


def test_git_status_filter_porcelain():
    status_text = (
        "## main...origin/main\n"
        "A  src/new.py\n"
        " M src/app.py\n"
        "?? untracked.txt\n"
        "UU conflict.py\n"
    )
    result = filter_git_status(status_text)
    assert "* main...origin/main" in result
    assert "+ Staged: 1 files" in result
    assert "~ Modified: 1 files" in result
    assert "? Untracked: 1 files" in result
    assert "conflicts: 1 files" in result


def test_git_status_filter_clean():
    result = filter_git_status("")
    assert result == "Clean working tree"


def test_git_log_filter():
    raw_log = (
        "commit 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b\n"
        "Author: Alice <alice@example.com>\n"
        "Date:   Fri Oct 23 12:00:00 2026 +0000\n"
        "\n"
        "    feat: add shiny feature\n"
        "\n"
        "    More detailed description of shiny feature.\n"
        " 1 file changed, 10 insertions(+)\n"
    )
    result = filter_git_log(raw_log)
    assert "commit 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b" in result
    assert "Author: Alice <alice@example.com>" in result
    assert "Subject: feat: add shiny feature" in result
    assert "1 file changed" in result
    assert "More detailed description" not in result


def test_build_output_filter():
    build_text = (
        "Compiling core v0.1.0\n"
        "Compiling utils v0.1.0\n"
        "Downloading package foo v1.0.0\n"
        "error[E0308]: mismatched types\n"
        "  --> src/main.rs:10:5\n"
        "warning: unused variable `x`\n"
        "BUILD FAILED in 2s\n"
    )
    result = filter_build_output(build_text)
    assert "Errors (2):" in result
    assert "mismatched types" in result
    assert "BUILD FAILED" in result
    assert "Warnings (1):" in result
    assert "unused variable `x`" in result
    assert "Compiling core" not in result


def test_grep_filter():
    grep_text = (
        "src/main.py:10:def run():\n"
        "src/main.py:15:    run()\n"
        "src/utils.py:42:def helper():\n"
    )
    result = filter_grep(grep_text)
    assert "3 matches in 2F:" in result
    assert "[file] src/main.py (2):" in result
    assert "  10: def run():" in result
    assert "[file] src/utils.py (1):" in result


def test_find_filter():
    find_text = (
        "src/components/Button.tsx\n"
        "src/components/Modal.tsx\n"
        "src/utils/math.ts\n"
    )
    result = filter_find(find_text)
    assert "3 files in 2 dirs:" in result
    assert "src/components/  (2)" in result
    assert "Button.tsx" in result
    assert "src/utils/  (1)" in result


def test_ls_filter():
    ls_text = (
        "total 32\n"
        "drwxr-xr-x  4 user group 4096 Oct 23 12:00 node_modules\n"
        "drwxr-xr-x  2 user group 4096 Oct 23 12:00 src\n"
        "-rw-r--r--  1 user group 1024 Oct 23 12:00 package.json\n"
        "-rw-r--r--  1 user group 2048 Oct 23 12:00 README.md\n"
    )
    result = filter_ls(ls_text)
    assert "node_modules" not in result
    assert "src/" in result
    assert "package.json  1.0K" in result
    assert "Summary: 2 files, 1 dirs" in result


def test_tree_filter():
    tree_text = (
        ".\n"
        "├── src\n"
        "│   └── main.py\n"
        "└── README.md\n"
        "\n"
        "1 directory, 2 files\n"
    )
    result = filter_tree(tree_text)
    assert "src" in result
    assert "main.py" in result
    assert "1 directory, 2 files" not in result


def test_dedup_log_filter():
    log_text = (
        "INFO: connecting to service\n"
        "WARN: connection retrying\n"
        "WARN: connection retrying\n"
        "WARN: connection retrying\n"
        "INFO: connected successfully\n"
    )
    result = filter_dedup_log(log_text)
    assert "WARN: connection retrying" in result
    assert "... (2 duplicate lines)" in result
    assert "INFO: connected successfully" in result


def test_smart_truncate_filter():
    lines = [f"log line {i}" for i in range(300)]
    text = "\n".join(lines)
    result = filter_smart_truncate(text)
    assert "... +120 lines truncated" in result
    assert "log line 0" in result
    assert "log line 299" in result


def test_read_numbered_filter():
    lines = [f" {i:4d}| const val_{i} = {i};" for i in range(300)]
    text = "\n".join(lines)
    result = filter_read_numbered(text)
    assert "... +120 lines truncated (file continues)" in result

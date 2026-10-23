#!/usr/bin/env bash
#
# pre_commit_lint.sh — shift CI lint left. Run it (via `make check`, formerly
# `make pre-commit`) before `git commit`, or after committing (e.g. a merge
# commit) to predict CI for the branch. It picks the files in scope and runs
# only the matching gating CI checks, so a clean run means a green CI lint:
#   - anything staged -> scope is the staged files; changed-but-unstaged files
#     whose checks were skipped are called out
#   - nothing staged  -> scope is the working tree's diff against the merge base
#     with origin's current default branch, untracked files included
# The per-area checks:
#   - litellm/ Python  -> `make lint` (test-linting.yml's python job)
#   - tests/e2e and tests/e2e_harness Python
#                      -> `make lint-e2e-basedpyright` (test-linting.yml's e2e type-check step)
#                         + raw HTTP client ban (test-linting.yml's check_e2e_no_raw_requests)
#   - tests/ Python, ruff-tests.toml, scripts/check_test_quality.py,
#     scripts/test_quality_gate.py
#                      -> ruff over ruff-tests.toml + `make lint-test-quality` (test-linting.yml's
#                         test-tree ruff and test-quality gate steps)
#   - proxy/types      -> regenerate the lazy OpenAPI snapshot, fail on drift (test-linting.yml's openapi-snapshot job)
#
# Each block is skipped when no matching files are in scope, so unrelated commits
# stay fast. This is intentionally not auto-installed as a git hook (see
# scripts/install_git_hooks.sh): the basedpyright pass can take
# minutes, so it's run on demand rather than firing on every human commit. It is
# hook-compatible if you want that anyway:
# `ln -s ../../scripts/pre_commit_lint.sh .git/hooks/pre-commit`.

set -eu

# Queue for one of the machine-wide heavy-work slots (see scripts/gate_slot_lock.py)
# before anything else, so N parallel `make check` runs across worktrees execute two
# at a time instead of thrashing the machine. The wrapper exports
# LITELLM_GATE_SLOT_HELD, so this re-exec happens exactly once and everything this
# script spawns (make lint, the lint gates) skips its own acquisition.
script_dir=$(python3 -c 'import os, sys; print(os.path.dirname(os.path.realpath(sys.argv[1])))' "$0")
if [ -z "${LITELLM_GATE_SLOT_HELD:-}" ]; then
    exec python3 "$script_dir/gate_slot_lock.py" "$0" "$@"
fi

if [ -z "${PRE_COMMIT_LINT_INNER:-}" ]; then
    log_file=$(git rev-parse --path-format=absolute --git-path pre_commit_lint.log)
    if : > "$log_file" 2>/dev/null; then
        echo "check: logging full output to $log_file"
        PRE_COMMIT_LINT_INNER=1 "$0" "$@" 2>&1 | tee "$log_file"
        pipe_status=("${PIPESTATUS[@]}")
        if [ "${pipe_status[1]}" -eq 0 ]; then
            echo "check: full log: $log_file"
        else
            echo "check: WARNING - writing $log_file failed; the log may be incomplete" >&2
        fi
        exit "${pipe_status[0]}"
    fi
    echo "check: WARNING - cannot write $log_file; output will not be saved" >&2
    PRE_COMMIT_LINT_INNER=1 exec "$0" "$@"
fi

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

staged=$(git diff --cached --name-only --diff-filter=ACMRD)
unstaged=$(git diff --name-only)
untracked=$(git ls-files --others --exclude-standard)

if [ -n "$staged" ]; then
    scope=$staged
else
    base_ref=$(python3 "$script_dir/default_branch.py" --base "${BASE_REF:-}") || {
        echo "check: FAIL"
        exit 1
    }
    export BASE_REF="$base_ref"
    merge_base=$(git merge-base "$base_ref" HEAD 2>/dev/null) || {
        echo "check: cannot resolve the merge base with $base_ref." >&2
        echo "  Fix: fetch the base ref and provide BASE_REF=<ref>" >&2
        echo "check: FAIL"
        exit 1
    }
    scope=$(printf '%s\n' "$(git diff --name-only --diff-filter=ACMRD "$merge_base")" "$untracked" | sed '/^$/d' | sort -u)
    if [ -z "$scope" ]; then
        echo "check: nothing to check (no staged files, no working-tree changes, no branch changes vs $base_ref)"
        echo "check: PASS"
        exit 0
    fi
    echo "check: nothing staged; scoping to the working tree's diff against the merge base with $base_ref:"
    printf '%s\n' "$scope" | sed 's/^/    /'
fi

scope_match() { printf '%s\n' "$scope" | grep -E "$1" || true; }

existing_files() {
    while IFS= read -r f; do
        if [ -f "$f" ]; then printf '%s\n' "$f"; fi
    done
}

litellm_py_pattern='^litellm/.*\.py$'
e2e_py_pattern='^tests/e2e(_harness)?/.*\.py$'
test_tree_pattern='^(tests/.*\.py|ruff-tests\.toml|scripts/(check_test_quality|test_quality_gate)\.py)$'
spec_pattern='^litellm/(proxy|types)/.*$'

litellm_py_files=$(scope_match "$litellm_py_pattern")
if [ -n "$(scope_match '^(litellm/proxy/_experimental/mcp_server/|scripts/check_mcp_operation_boundary\.py)')" ]; then
    uv run --no-sync python scripts/check_mcp_operation_boundary.py || exit 1
fi
e2e_py_files=$(scope_match "$e2e_py_pattern")
test_tree_files=$(scope_match "$test_tree_pattern")
# ruff format (and CI's format step) skip enterprise; the rest of make lint covers it.
fmt_files=$(printf '%s\n' "$litellm_py_files" | grep -v '^litellm/enterprise/' | existing_files)
# test-linting.yml's openapi-snapshot job triggers on any file under litellm/proxy
# or litellm/types (Prisma schema and configs included, not just Python), so match
# that whole trigger set rather than a Python subset.
spec_files=$(scope_match "$spec_pattern")

# CI lints the committed tree, so with staged files this script predicts CI for
# what you have STAGED (every trigger above reads `git diff --cached`). The tools
# it runs, though, read the working tree, so unstaged edits to tracked files and
# untracked files fold into the result and a green/red here won't match a commit
# of just the staged changes. There's no safe way to lint the index in place, so
# surface the gap instead of hiding it: stage everything you intend to commit
# before trusting a pass. This only warns; it never blocks or touches your changes.
if [ -n "$staged" ]; then
    not_staged=$(printf '%s\n' "$unstaged" "$untracked" | sed '/^$/d' | sort -u)
    if [ -n "$not_staged" ]; then
        echo "check: NOTE - unstaged/untracked changes are included in these checks but" >&2
        echo "  won't be in a commit of only your staged changes, so this result may differ from" >&2
        echo "  CI. Stage everything you intend to commit (git add) for an accurate prediction:" >&2
        printf '%s\n' "$not_staged" | sed 's/^/    /' >&2
    fi
    warn_skipped() {
        local check_name=$1 pattern=$2 triggered=$3
        [ -n "$triggered" ] && return 0
        local missed
        missed=$(printf '%s\n' "$not_staged" | grep -E "$pattern" || true)
        [ -z "$missed" ] && return 0
        echo "check: SKIPPED $check_name because these changed files are not staged:" >&2
        printf '%s\n' "$missed" | sed 's/^/    /' >&2
    }
    warn_skipped "Python lint (make lint)" "$litellm_py_pattern" "$litellm_py_files"
    warn_skipped "tests/e2e checks (basedpyright + raw HTTP client ban)" "$e2e_py_pattern" "$e2e_py_files"
    warn_skipped "test-tree lint (ruff-tests.toml + test-quality gate)" "$test_tree_pattern" "$test_tree_files"
    warn_skipped "lazy OpenAPI snapshot sync" "$spec_pattern" "$spec_files"
fi

status=0

bootstrap_hint() {
    echo "  This checkout looks unprovisioned (fresh worktree or clone)." >&2
    echo "  Fix: make bootstrap" >&2
}

python_checks() {
    local rc=0
    echo "check: linting Python (make lint)"
    make lint || { echo "✗ Python lint failed. Fix the reds above, then re-run make check." >&2; rc=1; }
    # `make lint` format-checks files in origin/base...HEAD, which at pre-commit time
    # predates the staged change, so format-check the scoped litellm files directly to
    # cover a brand-new commit before it lands.
    if [ -n "$fmt_files" ]; then
        echo "check: ruff format --check (scoped litellm files)"
        printf '%s\n' "$fmt_files" | xargs uv run --no-sync ruff format --check --exclude '/enterprise/' \
            || { echo "✗ Unformatted files in scope. Fix with: make format, then re-stage." >&2; rc=1; }
    fi
    return $rc
}

on_interrupt() {
    trap - INT TERM
    rm -f "${python_log:-}" "${gen_log:-}"
    for job_pid in ${python_pid:-} ${gen_pid:-}; do
        kill -- "-$job_pid" 2>/dev/null || true
    done
    exit 130
}
trap on_interrupt INT TERM

if [ -n "$litellm_py_files" ]; then
    python_log=$(mktemp)
    set -m
    python_checks > "$python_log" 2>&1 &
    python_pid=$!
    set +m
fi

if [ -n "$e2e_py_files" ] && [ -z "$litellm_py_files" ]; then
    echo "check: type-checking tests/e2e (make lint-e2e-basedpyright)"
    make lint-e2e-basedpyright || { echo "✗ tests/e2e basedpyright failed. Fix the errors above, then re-run make check." >&2; status=1; }
fi

if [ -n "$e2e_py_files" ]; then
    echo "check: checking tests/e2e raw HTTP client ban (check_e2e_no_raw_requests)"
    uv run --no-sync python tests/code_coverage_tests/check_e2e_no_raw_requests.py \
        || { echo "✗ Raw HTTP client import in tests/e2e. Route the call through tests/e2e/e2e_http.py, then re-run make check." >&2; status=1; }
fi

snapshot_checks() {
    local status=0
    echo "check: checking the lazy OpenAPI snapshot is in sync"
    # Regenerating the snapshot imports litellm.proxy.proxy_server, which needs the
    # proxy deps and an up-to-date Prisma client; test-linting.yml installs those and
    # runs prisma generate before regenerating, so mirror that here or a stale client
    # can mask drift that CI will still flag.
    if ! uv run --no-sync python -c "import orjson, prisma" 2>/dev/null; then
        echo "✗ The Python env lacks the proxy deps (orjson/prisma) that the snapshot check needs." >&2
        bootstrap_hint
        status=1
    elif ! uv run --no-sync python scripts/prisma_generate_if_needed.py; then
        echo "✗ Could not regenerate Prisma client (prisma generate failed)." >&2
        status=1
    elif ! uv run --no-sync python -m litellm.proxy._lazy_openapi_snapshot; then
        echo "✗ Could not regenerate the lazy OpenAPI snapshot (python -m litellm.proxy._lazy_openapi_snapshot failed)." >&2
        status=1
    elif ! git diff --quiet -- litellm/proxy/_lazy_openapi_snapshot.json; then
        echo "✗ The lazy OpenAPI snapshot is stale; regenerated litellm/proxy/_lazy_openapi_snapshot.json. Stage it and commit; re-run make check only if other checks failed too." >&2
        status=1
    fi
    return $status
}

if [ -n "$spec_files" ]; then
    gen_log=$(mktemp)
    set -m
    snapshot_checks > "$gen_log" 2>&1 &
    gen_pid=$!
    set +m
fi

if [ -n "$test_tree_files" ] && [ -z "$litellm_py_files" ]; then
    echo "check: linting the test tree (ruff check --config ruff-tests.toml tests)"
    uv run --no-sync ruff check --config ruff-tests.toml tests \
        || { echo "✗ Test-tree ruff failed. Fix the errors above, then re-run make check." >&2; status=1; }
    echo "check: checking the test-quality gate (make lint-test-quality)"
    make lint-test-quality \
        || { echo "✗ Test-quality gate failed. Fix the errors above, then re-run make check." >&2; status=1; }
fi

if [ -n "${python_pid:-}" ]; then
    wait "$python_pid" || status=1
    cat "$python_log"; rm -f "$python_log"
fi
if [ -n "${gen_pid:-}" ]; then
    wait "$gen_pid" || status=1
    cat "$gen_log"; rm -f "$gen_log"
fi

summary_item() {
    local check_name=$1 triggered=$2 skip_reason=$3
    if [ -n "$triggered" ]; then
        echo "    ran:     $check_name"
    else
        echo "    skipped: $check_name ($skip_reason)"
    fi
}

echo "check: summary"
summary_item "Python lint (make lint)" "$litellm_py_files" "no litellm/ Python files in scope"
summary_item "tests/e2e checks (basedpyright + raw HTTP client ban)" "$e2e_py_files" "no tests/e2e Python files in scope"
summary_item "test-tree lint (ruff-tests.toml + test-quality gate)" "$test_tree_files" \
    "no tests/ Python files or test-tree lint inputs in scope"
summary_item "lazy OpenAPI snapshot sync" "$spec_files" "no litellm/proxy or litellm/types files in scope"

if [ -z "$litellm_py_files$e2e_py_files$test_tree_files$spec_files" ]; then
    echo "check: NOTE - no gating lint check matches the files in scope, so nothing ran:" >&2
    printf '%s\n' "$scope" | sed 's/^/    /' >&2
    echo "  A pass here is a no-op, not a lint verdict." >&2
fi

if [ "$status" -eq 0 ]; then
    echo "check: PASS"
else
    echo "check: FAIL"
fi
exit $status

#!/usr/bin/env bash
# Transactional upstream sync for the SEO OMP fork.
#
# Contract:
# 1. Preflight: checks clean tracked working tree and absence of an in-progress merge
#    using 'git rev-parse --git-path MERGE_HEAD' (supports worktrees).
# 2. Fetch: fetches the upstream ref and fast-forwards the local mirror branch if it is a branch.
# 3. Already up-to-date: if the target is already an ancestor of HEAD, exits 0 cleanly.
# 4. Transactional merge: merges with '--no-ff --no-commit'. HEAD is NOT moved prematurely.
# 5. Collision handling: if merge fails without MERGE_HEAD (untracked collision), does not advise abort.
# 6. Adapt & Validate: runs adapt, verify, and self-audit while the merge is staged.
#    - On failure: exits non-zero (4) leaving MERGE_HEAD intact so the merge can be
#      cleanly aborted via 'git merge --abort'.
#    - On success: stages the tracked adaptation delta ('git add -u') so index matches
#      worktree (no MM dirty state), asserts 'git diff --quiet', and leaves the validated
#      merge pending for explicit review and commit. Never auto-commits or pushes.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

REMOTE="${OMP_UPSTREAM_REMOTE:-upstream}"
REF=""
SKIP_POST=0
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: omp/sync-upstream.sh [REF] [options]

Fetch REF from upstream and merge it transactionally into the current branch.

Contract:
  - Merges with '--no-ff --no-commit' so HEAD is never prematurely committed before checks.
  - Re-applies OMP adaptation and runs verification suite while merge is staged.
  - On postcheck failure, exits non-zero leaving merge state recoverable via 'git merge --abort'.
  - On success, stages tracked adaptation delta so index matches worktree, pending explicit commit.
  - Never auto-commits, never auto-adds untracked files, and never pushes.

Arguments:
  REF                 Branch or tag to sync (default: main)

Options:
  --remote REMOTE     Upstream remote name (default: upstream, env OMP_UPSTREAM_REMOTE)
  --skip-post         Merge only; skip adapt/verify/self-audit
  --dry-run           Print the plan without fetching or merging
  -h, --help          Show this help

Exit codes:
  0: Success (or already up-to-date)
  1: Generic invocation error
  2: Dirty working tree, merge already in progress, or untracked file collision
  3: Merge conflict (recoverable via git merge --abort)
  4: Post-merge validation failure (recoverable via git merge --abort)
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --remote)
            [ "$#" -ge 2 ] || die "--remote requires a value"
            REMOTE="$2"
            shift 2
            ;;
        --skip-post)
            SKIP_POST=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            die "unknown option: $1"
            ;;
        *)
            [ -z "${REF}" ] || die "only one REF may be given"
            REF="$1"
            shift
            ;;
    esac
done
REF="${REF:-main}"

git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git repository: ${REPO_ROOT}"
git remote get-url "${REMOTE}" >/dev/null 2>&1 || die "git remote '${REMOTE}' is not configured"

CURRENT_BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[ -n "${CURRENT_BRANCH}" ] || die "detached HEAD; checkout a branch before syncing"
[ "${CURRENT_BRANCH}" != "${REF}" ] || die "refusing to merge ${REF} into itself; checkout omp-compat first"
if [ "${DRY_RUN}" -eq 1 ]; then
    echo "==> Dry run: would fetch ${REMOTE} --tags, fast-forward '${REF}' when it is a branch,"
    echo "    then transactionally merge '${REF}' into '${CURRENT_BRANCH}' with --no-ff --no-commit,"
    echo "    and run adapt/verify/self-audit."
    exit 0
fi

MERGE_HEAD_PATH="$(git rev-parse --git-path MERGE_HEAD)"
if [ -f "${MERGE_HEAD_PATH}" ]; then
    echo "ERROR: a merge is already in progress (${MERGE_HEAD_PATH} exists). Complete it with 'git commit' or abort with 'git merge --abort' before syncing." >&2
    exit 2
fi

if [ -n "$(git status --porcelain=v1 --untracked-files=no)" ]; then
    echo "ERROR: working tree has uncommitted tracked changes; commit or stash them before syncing." >&2
    git status --short --untracked-files=no >&2
    exit 2
fi

echo "==> Fetching ${REMOTE} (ref: ${REF})"
echo "    repo:   ${REPO_ROOT}"
echo "    branch: ${CURRENT_BRANCH}"

git fetch "${REMOTE}" --tags

TARGET=""
if git rev-parse --verify --quiet "refs/remotes/${REMOTE}/${REF}" >/dev/null; then
    # Keep the local mirror branch current (fast-forward only).
    if ! git fetch "${REMOTE}" "${REF}:refs/heads/${REF}"; then
        die "could not fast-forward local '${REF}' from '${REMOTE}/${REF}'; local commits diverge"
    fi
    TARGET="refs/heads/${REF}"
elif git rev-parse --verify --quiet "refs/tags/${REF}" >/dev/null; then
    TARGET="refs/tags/${REF}"
else
    die "ref '${REF}' not found in ${REMOTE} (neither branch nor tag)"
fi

# Repeated no-update check: if target is already an ancestor of HEAD and tree is clean
if git merge-base --is-ancestor "${TARGET}" HEAD; then
    echo "==> Already up to date with ${TARGET}."
    if [ "${SKIP_POST}" -eq 0 ]; then
        PYTHON_BIN="${PYTHON_BIN:-python3}"
        echo "==> Running verification on current state"
        "${PYTHON_BIN}" "${REPO_ROOT}/omp/verify.py" || die "omp/verify.py failed on current state"
    fi
    exit 0
fi

echo "==> Merging ${TARGET} into ${CURRENT_BRANCH} (--no-ff --no-commit)"
set +e
git merge --no-ff --no-commit "${TARGET}"
MERGE_RC=$?
set -e
if [ "${MERGE_RC}" -ne 0 ]; then
    echo "ERROR: merge failed (exit ${MERGE_RC})." >&2
    MERGE_HEAD_PATH="$(git rev-parse --git-path MERGE_HEAD)"
    if [ -f "${MERGE_HEAD_PATH}" ]; then
        git diff --name-only --diff-filter=U 2>/dev/null | sed 's/^/  conflict: /' >&2 || true
        echo "  Resolve the conflicts, or abandon with: git merge --abort" >&2
        exit 3
    else
        echo "  No merge in progress (e.g. untracked file collision). Move or remove colliding files before syncing." >&2
        exit 2
    fi
fi

if [ "${SKIP_POST}" -eq 1 ]; then
    echo "==> Merge staged (--no-commit). Post-merge checks skipped."
    echo "    Review with 'git diff --cached' and commit with 'git commit'."
    MERGE_HEAD_PATH="$(git rev-parse --git-path MERGE_HEAD)"
    if [ -f "${MERGE_HEAD_PATH}" ]; then
        echo "    To abandon: git merge --abort"
    fi
    exit 0
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "==> Re-applying OMP adaptation layer"
if ! "${PYTHON_BIN}" "${REPO_ROOT}/omp/adapt.py"; then
    echo "ERROR: omp/adapt.py failed after merge." >&2
    MERGE_HEAD_PATH="$(git rev-parse --git-path MERGE_HEAD)"
    if [ -f "${MERGE_HEAD_PATH}" ]; then
        echo "  Merge is in progress (${MERGE_HEAD_PATH} exists). Review issues or run 'git merge --abort'." >&2
    fi
    exit 4
fi

echo "==> Running verification"
if ! "${PYTHON_BIN}" "${REPO_ROOT}/omp/verify.py"; then
    echo "ERROR: omp/verify.py failed after merge." >&2
    MERGE_HEAD_PATH="$(git rev-parse --git-path MERGE_HEAD)"
    if [ -f "${MERGE_HEAD_PATH}" ]; then
        echo "  Merge is in progress (${MERGE_HEAD_PATH} exists). Review issues or run 'git merge --abort'." >&2
    fi
    exit 4
fi

echo "==> Running self-audit"
if ! "${PYTHON_BIN}" "${REPO_ROOT}/omp/self_audit.py"; then
    echo "ERROR: omp/self_audit.py failed after merge." >&2
    MERGE_HEAD_PATH="$(git rev-parse --git-path MERGE_HEAD)"
    if [ -f "${MERGE_HEAD_PATH}" ]; then
        echo "  Merge is in progress (${MERGE_HEAD_PATH} exists). Review issues or run 'git merge --abort'." >&2
    fi
    exit 4
fi

# Stage tracked adaptation delta so the index matches the worktree exactly
git add -u
git diff --quiet || die "index does not match worktree after staging adaptation delta"

echo "==> Upstream sync and verification succeeded."
echo "    Merge and adaptation are staged in the index (index matches worktree)."
echo "    Review changes with 'git diff --cached' and commit with: git commit"
echo "    To abandon the sync: git merge --abort"

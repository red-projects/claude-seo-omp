#!/usr/bin/env python3
"""
Focused regression tests for the transactional upstream sync workflow (SEO).

Verifies the transactional contract:
1. Transactional merge: merges with '--no-ff --no-commit'. HEAD is NOT prematurely committed.
2. Clean merge: incoming files staged, uncommitted, pending review.
3. Adapt modification staging: adapt.py modifying tracked files has its delta staged (M , not MM),
   index matches worktree (git diff --quiet passes), and untracked user files remain unstaged.
4. Untracked file collision: when merge fails due to untracked collision, no MERGE_HEAD is created,
   script exits 2, and does not advise git merge --abort.
5. Worktree compatibility: works inside git worktrees (where .git is a file, not a directory).
6. Dirty tree rejection: refuses to run on uncommitted changes (exit code 2).
7. Conflict handling: on conflict exits with code 3, leaves MERGE_HEAD and unmerged status.
8. Postcheck failure: when verify/adapt fails, exits non-zero (code 4), HEAD is unchanged,
   and MERGE_HEAD remains intact so the merge can be cleanly aborted via 'git merge --abort'.
9. Repeated no-update case: when HEAD already incorporates upstream, exits 0 cleanly without
   creating an empty merge or failing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = REPO_ROOT / "omp" / "sync-upstream.sh"


def run(cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def setup_fixture() -> tuple[Path, Path, Path]:
    tmpdir = Path(tempfile.mkdtemp(prefix="omp_reg_seo_"))
    upstream = tmpdir / "upstream"
    local = tmpdir / "local"
    upstream.mkdir()

    run(["git", "init", "-b", "main"], upstream, check=True)
    run(["git", "config", "user.name", "Upstream Dev"], upstream, check=True)
    run(["git", "config", "user.email", "dev@upstream.org"], upstream, check=True)
    (upstream / "data.txt").write_text("v1\n")
    run(["git", "add", "."], upstream, check=True)
    run(["git", "commit", "-m", "Initial upstream commit"], upstream, check=True)

    run(["git", "clone", str(upstream), str(local)], tmpdir, check=True)
    run(["git", "config", "user.name", "OMP Dev"], local, check=True)
    run(["git", "config", "user.email", "omp@omarchy.org"], local, check=True)
    run(["git", "remote", "rename", "origin", "upstream"], local, check=True)
    run(["git", "checkout", "-b", "omp-compat"], local, check=True)
    (local / "omp-compat.txt").write_text("compat\n")
    run(["git", "add", "."], local, check=True)
    run(["git", "commit", "-m", "Local compat commit"], local, check=True)

    omp_dir = local / "omp"
    omp_dir.mkdir(exist_ok=True)
    shutil.copy2(SYNC_SCRIPT, omp_dir / "sync-upstream.sh")
    (omp_dir / "sync-upstream.sh").chmod(0o755)

    (omp_dir / "adapt.py").write_text("#!/usr/bin/env python3\nprint('adapt ok')\n")
    (omp_dir / "verify.py").write_text("#!/usr/bin/env python3\nprint('verify ok')\n")
    (omp_dir / "self_audit.py").write_text("#!/usr/bin/env python3\nprint('audit ok')\n")
    (omp_dir / "adapt.py").chmod(0o755)
    (omp_dir / "verify.py").chmod(0o755)
    (omp_dir / "self_audit.py").chmod(0o755)

    run(["git", "add", "."], local, check=True)
    run(["git", "commit", "-m", "Add sync tools"], local, check=True)

    return tmpdir, upstream, local


def test_clean_transactional_merge() -> None:
    tmpdir, upstream, local = setup_fixture()
    try:
        (upstream / "new_feature.txt").write_text("feature\n")
        run(["git", "add", "."], upstream, check=True)
        run(["git", "commit", "-m", "Upstream feature"], upstream, check=True)

        pre_head = run(["git", "rev-parse", "HEAD"], local, check=True).stdout.strip()
        res = run(["bash", "omp/sync-upstream.sh", "main"], local)
        assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"

        post_head = run(["git", "rev-parse", "HEAD"], local, check=True).stdout.strip()
        assert pre_head == post_head, f"Transactional contract violated: HEAD moved before explicit commit! {pre_head} != {post_head}"

        merge_head_path = run(["git", "rev-parse", "--git-path", "MERGE_HEAD"], local, check=True).stdout.strip()
        assert (local / merge_head_path).exists(), "Expected MERGE_HEAD to exist for uncommitted staged merge"

        status = run(["git", "status", "--porcelain"], local, check=True).stdout
        assert "A  new_feature.txt" in status, f"Expected staged file, got:\n{status}"
    finally:
        shutil.rmtree(tmpdir)


def test_adapt_modifying_tracked_file_stages_cleanly() -> None:
    tmpdir, upstream, local = setup_fixture()
    try:
        # Upstream adds a feature
        (upstream / "feature.txt").write_text("feature from upstream\n")
        run(["git", "add", "."], upstream, check=True)
        run(["git", "commit", "-m", "Upstream feature"], upstream, check=True)

        # adapt.py modifies a tracked file (e.g. omp-compat.txt)
        adapt_script = (
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "p = Path('omp-compat.txt')\n"
            "p.write_text('compat modified by adapt.py\\n')\n"
            "print('adapt modified tracked file')\n"
        )
        (local / "omp" / "adapt.py").write_text(adapt_script)
        run(["git", "add", "omp/adapt.py"], local, check=True)
        run(["git", "commit", "-m", "Update adapt.py to modify tracked file"], local, check=True)

        # User has an untracked file
        (local / "user_scratchpad.tmp").write_text("user notes\n")

        res = run(["bash", "omp/sync-upstream.sh", "main"], local)
        assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"

        # 1. Unstaged tracked diff must be empty (index matches worktree)
        diff_res = run(["git", "diff", "--quiet"], local)
        assert diff_res.returncode == 0, "Unstaged tracked diff exists! Index does not match worktree."

        # 2. Check porcelain status:
        # - omp-compat.txt should be 'M ' (staged), NOT 'MM' (partially staged)
        # - feature.txt should be 'A ' (staged)
        # - user_scratchpad.tmp should be '??' (untracked, never staged)
        status = run(["git", "status", "--porcelain"], local, check=True).stdout
        lines = [line.strip() for line in status.splitlines()]
        assert "M  omp-compat.txt" in lines, f"Expected 'M  omp-compat.txt' in status, got:\n{status}"
        assert "A  feature.txt" in lines, f"Expected 'A  feature.txt' in status, got:\n{status}"
        assert "?? user_scratchpad.tmp" in lines, f"User untracked file was improperly staged! Got:\n{status}"
    finally:
        shutil.rmtree(tmpdir)


def test_untracked_collision_handling() -> None:
    tmpdir, upstream, local = setup_fixture()
    try:
        (upstream / "colliding.txt").write_text("from upstream\n")
        run(["git", "add", "."], upstream, check=True)
        run(["git", "commit", "-m", "Upstream collision"], upstream, check=True)

        # Create an untracked local file with same name
        (local / "colliding.txt").write_text("local untracked\n")

        res = run(["bash", "omp/sync-upstream.sh", "main"], local)
        assert res.returncode == 2, f"Expected code 2 for untracked collision, got {res.returncode}"
        assert "No merge in progress" in res.stderr
        assert "git merge --abort" not in res.stderr

        merge_head_path = run(["git", "rev-parse", "--git-path", "MERGE_HEAD"], local, check=True).stdout.strip()
        assert not (local / merge_head_path).exists(), "MERGE_HEAD must not exist on untracked collision"
    finally:
        shutil.rmtree(tmpdir)


def test_worktree_support() -> None:
    tmpdir, upstream, local = setup_fixture()
    try:
        wt_dir = tmpdir / "local_worktree"
        run(["git", "worktree", "add", "-b", "omp-wt", str(wt_dir), "omp-compat"], local, check=True)
        # Verify .git is a file in worktree
        assert (wt_dir / ".git").is_file()

        (upstream / "wt_feature.txt").write_text("wt feature\n")
        run(["git", "add", "."], upstream, check=True)
        run(["git", "commit", "-m", "Upstream wt feature"], upstream, check=True)

        res = run(["bash", "omp/sync-upstream.sh", "main"], wt_dir)
        assert res.returncode == 0, f"Expected 0 in worktree, got {res.returncode}: {res.stderr}"

        merge_head_path = run(["git", "rev-parse", "--git-path", "MERGE_HEAD"], wt_dir, check=True).stdout.strip()
        assert Path(merge_head_path).exists(), "MERGE_HEAD must resolve and exist in worktree"
    finally:
        shutil.rmtree(tmpdir)


def test_dirty_tree_rejection() -> None:
    tmpdir, upstream, local = setup_fixture()
    try:
        (local / "omp-compat.txt").write_text("dirty\n")
        res = run(["bash", "omp/sync-upstream.sh", "main"], local)
        assert res.returncode == 2, f"Expected 2 for dirty tree, got {res.returncode}"
        assert "uncommitted tracked changes" in res.stderr
    finally:
        shutil.rmtree(tmpdir)


def test_conflict_handling() -> None:
    tmpdir, upstream, local = setup_fixture()
    try:
        (upstream / "data.txt").write_text("conflict upstream\n")
        run(["git", "add", "."], upstream, check=True)
        run(["git", "commit", "-m", "Upstream conflict"], upstream, check=True)

        (local / "data.txt").write_text("conflict local\n")
        run(["git", "add", "."], local, check=True)
        run(["git", "commit", "-m", "Local conflict"], local, check=True)

        res = run(["bash", "omp/sync-upstream.sh", "main"], local)
        assert res.returncode == 3, f"Expected 3 for conflict, got {res.returncode}"
        merge_head_path = run(["git", "rev-parse", "--git-path", "MERGE_HEAD"], local, check=True).stdout.strip()
        assert (local / merge_head_path).exists(), "MERGE_HEAD must exist on conflict"
        status = run(["git", "status", "--porcelain"], local, check=True).stdout
        assert "UU data.txt" in status, f"Expected UU conflict, got:\n{status}"

        run(["git", "merge", "--abort"], local, check=True)
    finally:
        shutil.rmtree(tmpdir)


def test_postcheck_failure_recovery() -> None:
    tmpdir, upstream, local = setup_fixture()
    try:
        # Induce failure in verify.py
        (local / "omp" / "verify.py").write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(42)\n")
        run(["git", "add", "."], local, check=True)
        run(["git", "commit", "-m", "Commit failing verify.py"], local, check=True)

        (upstream / "feature.txt").write_text("feature\n")
        run(["git", "add", "."], upstream, check=True)
        run(["git", "commit", "-m", "Feature"], upstream, check=True)

        pre_head = run(["git", "rev-parse", "HEAD"], local, check=True).stdout.strip()
        res = run(["bash", "omp/sync-upstream.sh", "main"], local)
        assert res.returncode == 4, f"Expected 4 for postcheck failure, got {res.returncode}"

        post_head = run(["git", "rev-parse", "HEAD"], local, check=True).stdout.strip()
        assert pre_head == post_head, "HEAD must not move when postchecks fail"

        merge_head_path = run(["git", "rev-parse", "--git-path", "MERGE_HEAD"], local, check=True).stdout.strip()
        assert (local / merge_head_path).exists(), "MERGE_HEAD must exist on failure"

        # Verify abort returns to clean state
        run(["git", "merge", "--abort"], local, check=True)
        assert not (local / merge_head_path).exists()
        clean_status = run(["git", "status", "--porcelain"], local, check=True).stdout
        assert clean_status.strip() == "", f"Expected clean status after abort, got: {clean_status}"
    finally:
        shutil.rmtree(tmpdir)


def test_repeated_no_update_case() -> None:
    tmpdir, upstream, local = setup_fixture()
    try:
        (upstream / "feature.txt").write_text("feature\n")
        run(["git", "add", "."], upstream, check=True)
        run(["git", "commit", "-m", "Feature"], upstream, check=True)

        # Sync 1: stages merge
        res1 = run(["bash", "omp/sync-upstream.sh", "main"], local)
        assert res1.returncode == 0

        # Operator completes commit as instructed
        run(["git", "commit", "-m", "chore: sync with upstream"], local, check=True)
        head_after_commit = run(["git", "rev-parse", "HEAD"], local, check=True).stdout.strip()

        # Sync 2: repeated invocation when already up to date
        res2 = run(["bash", "omp/sync-upstream.sh", "main"], local)
        assert res2.returncode == 0, f"Expected 0 for repeated no-update, got {res2.returncode}: {res2.stderr}"
        assert "Already up to date" in res2.stdout

        head_after_repeat = run(["git", "rev-parse", "HEAD"], local, check=True).stdout.strip()
        assert head_after_commit == head_after_repeat, "HEAD must remain unchanged when already up to date"
        merge_head_path = run(["git", "rev-parse", "--git-path", "MERGE_HEAD"], local, check=True).stdout.strip()
        assert not (local / merge_head_path).exists()
    finally:
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    print("Running SEO sync workflow regression tests...")
    test_clean_transactional_merge()
    print("  ✓ test_clean_transactional_merge: PASS")
    test_adapt_modifying_tracked_file_stages_cleanly()
    print("  ✓ test_adapt_modifying_tracked_file_stages_cleanly: PASS")
    test_untracked_collision_handling()
    print("  ✓ test_untracked_collision_handling: PASS")
    test_worktree_support()
    print("  ✓ test_worktree_support: PASS")
    test_dirty_tree_rejection()
    print("  ✓ test_dirty_tree_rejection: PASS")
    test_conflict_handling()
    print("  ✓ test_conflict_handling: PASS")
    test_postcheck_failure_recovery()
    print("  ✓ test_postcheck_failure_recovery: PASS")
    test_repeated_no_update_case()
    print("  ✓ test_repeated_no_update_case: PASS")
    print("ALL 8 REGRESSION TESTS PASSED.")

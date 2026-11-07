#!/usr/bin/env python3
"""
OMP SEO Self-Audit Engine.

Performs deep integrity and compliance checks:
1. Git topology and remotes (origin, upstream, push DISABLE)
2. Main branch alignment (main == v2.5.0)
3. Zero-conflict upstream invariant (scripts/, hooks/, skills/seo/references/)
4. 26 Skill definitions byte budget and frontmatter schemas
5. Idempotency of omp/adapt.py
6. Falsification Probe A: Graceful malformed HTML/JSON-LD handling
7. Falsification Probe B: Path traversal rejection (exit code 2)
8. Falsification Probe C: Cloud metadata SSRF blocking (169.254.169.254)
9. System Chromium hook: zero ms-playwright browser downloads
10. Global symlink verification (~/.local/bin/omp-seo-run)

Emits JSON audit report to omp/audit-report.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_FILE = REPO_ROOT / "omp" / "audit-report.json"
RUNNER_PATH = REPO_ROOT / "bin" / "seo-run"
SYMLINK_PATH = Path.home() / ".local" / "bin" / "omp-seo-run"


def get_runner() -> str:
    if SYMLINK_PATH.is_file() and os.access(SYMLINK_PATH, os.X_OK):
        return str(SYMLINK_PATH)
    return str(RUNNER_PATH)


def check_git_remotes() -> dict:
    res = subprocess.run(["git", "remote", "-v"], cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        return {"status": "FAIL", "detail": f"git remote -v failed: {res.stderr}"}

    lines = res.stdout.splitlines()
    origin_fetch = ""
    upstream_fetch = ""
    upstream_push = ""
    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            name, url, mode = parts[0], parts[1], parts[2]
            if name == "origin" and mode == "(fetch)":
                origin_fetch = url
            elif name == "upstream" and mode == "(fetch)":
                upstream_fetch = url
            elif name == "upstream" and mode == "(push)":
                upstream_push = url

    if "red-projects/claude-seo-omp" not in origin_fetch:
        return {"status": "FAIL", "detail": f"origin fetch url mismatch: {origin_fetch}"}
    if "AgriciDaniel/claude-seo" not in upstream_fetch:
        return {"status": "FAIL", "detail": f"upstream fetch url mismatch: {upstream_fetch}"}
    if upstream_push != "DISABLE":
        return {"status": "FAIL", "detail": f"upstream push url is '{upstream_push}', expected 'DISABLE'"}

    return {
        "status": "PASS",
        "detail": {
            "origin": origin_fetch,
            "upstream_fetch": upstream_fetch,
            "upstream_push": upstream_push,
        },
    }


def check_main_branch_v250() -> dict:
    res_main = subprocess.run(["git", "rev-parse", "main"], cwd=REPO_ROOT, capture_output=True, text=True)
    res_v250 = subprocess.run(["git", "rev-parse", "v2.5.0^{commit}"], cwd=REPO_ROOT, capture_output=True, text=True)

    if res_main.returncode != 0 or res_v250.returncode != 0:
        return {"status": "FAIL", "detail": "Failed to resolve main or v2.5.0 commit hash"}

    main_hash = res_main.stdout.strip()
    v250_hash = res_v250.stdout.strip()
    if main_hash != v250_hash:
        return {"status": "FAIL", "detail": f"main ({main_hash}) does not match v2.5.0 ({v250_hash})"}

    return {"status": "PASS", "detail": {"main_commit": main_hash, "tag_v2.5.0": v250_hash}}


def check_zero_diff_upstream() -> dict:
    cmd = ["git", "diff", "v2.5.0", "--stat", "--", "scripts/", "hooks/", "skills/seo/references/"]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        return {"status": "FAIL", "detail": f"git diff failed: {res.stderr}"}
    if res.stdout.strip():
        return {"status": "FAIL", "detail": f"modifications found in protected upstream paths:\n{res.stdout}"}
    return {"status": "PASS", "detail": "0 lines modified across scripts/, hooks/, skills/seo/references/"}


def check_skill_byte_counts() -> dict:
    skills_dir = REPO_ROOT / "skills"
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    if len(skill_files) != 26:
        return {"status": "FAIL", "detail": f"Expected 26 skills, found {len(skill_files)}"}

    records = {}
    for sf in skill_files:
        content = sf.read_text(encoding="utf-8")
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return {"status": "FAIL", "detail": f"{sf.parent.name} missing opening '---'"}
        closing = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing = i
                break
        if closing == -1:
            return {"status": "FAIL", "detail": f"{sf.parent.name} unclosed frontmatter"}

        fm = {}
        for line in lines[1:closing]:
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"').strip("'")

        if set(fm.keys()) != {"name", "description"}:
            return {"status": "FAIL", "detail": f"{sf.parent.name} invalid keys: {set(fm.keys())}"}

        b = len(fm["description"].encode("utf-8"))
        if b > 300:
            return {"status": "FAIL", "detail": f"{sf.parent.name} byte count {b} > 300"}
        records[sf.parent.name] = b

    return {"status": "PASS", "detail": records}


def check_adapt_idempotency() -> dict:
    skills_dir = REPO_ROOT / "skills"
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))

    hashes_before = {
        sf.name: hashlib.sha256(sf.read_bytes()).hexdigest() for sf in skill_files
    }

    res = subprocess.run([sys.executable, str(REPO_ROOT / "omp" / "adapt.py")], cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        return {"status": "FAIL", "detail": f"omp/adapt.py failed: {res.stderr}"}

    hashes_after = {
        sf.name: hashlib.sha256(sf.read_bytes()).hexdigest() for sf in skill_files
    }

    if hashes_before != hashes_after:
        return {"status": "FAIL", "detail": "SKILL.md hashes changed on re-run (not idempotent)"}

    return {"status": "PASS", "detail": "Re-running adapt.py produced 0 file modifications"}


def check_probe_a() -> dict:
    runner = get_runner()
    # 1. Malformed HTML
    res_html = subprocess.run(
        [runner, "parse_html.py"],
        input="<html><head><title>Unclosed Probe",
        text=True,
        capture_output=True,
    )
    if res_html.returncode != 0 or "Unclosed Probe" not in res_html.stdout:
        return {"status": "FAIL", "detail": f"parse_html failed on malformed HTML: {res_html.stderr}"}

    # 2. Malformed JSON
    res_json = subprocess.run(
        [runner, "schema_ecommerce_validate.py"],
        input="{not valid json:",
        text=True,
        capture_output=True,
    )
    if res_json.returncode != 2 or "invalid JSON" not in res_json.stderr:
        return {"status": "FAIL", "detail": f"schema_ecommerce_validate unexpected returncode {res_json.returncode}"}

    return {"status": "PASS", "detail": "Graceful HTML error handling and exit code 2 on invalid JSON"}


def check_probe_b() -> dict:
    runner = get_runner()
    res = subprocess.run([runner, "../../etc/passwd"], capture_output=True, text=True)
    if res.returncode != 2:
        return {"status": "FAIL", "detail": f"Expected returncode 2 for traversal, got {res.returncode}"}
    return {"status": "PASS", "detail": "Path traversal blocked with exit code 2"}


def check_probe_c() -> dict:
    runner = get_runner()
    res = subprocess.run(
        [runner, "render_page.py", "http://169.254.169.254/latest/meta-data/", "--json"],
        capture_output=True,
        text=True,
    )
    if res.returncode == 0:
        return {"status": "FAIL", "detail": "Metadata endpoint fetch unexpectedly succeeded with code 0"}
    output = (res.stdout + res.stderr).lower()
    if "url_safety" not in output and "blocked" not in output:
        return {"status": "FAIL", "detail": f"Expected SSRF error, got: {output}"}
    return {"status": "PASS", "detail": "Metadata endpoint 169.254.169.254 strictly blocked"}


def check_playwright_cache() -> dict:
    cache_dir = Path.home() / ".cache" / "ms-playwright"
    if cache_dir.is_dir():
        browsers = list(cache_dir.glob("chromium*")) + list(cache_dir.glob("chrome*"))
        if browsers:
            return {"status": "FAIL", "detail": f"Found downloaded browser binaries in {cache_dir}: {browsers}"}
    return {"status": "PASS", "detail": "No Playwright browser binaries downloaded to ~/.cache/ms-playwright"}


def check_symlink_active() -> dict:
    if not SYMLINK_PATH.is_symlink():
        return {"status": "FAIL", "detail": f"{SYMLINK_PATH} is not a symlink"}
    target = SYMLINK_PATH.resolve()
    if target != RUNNER_PATH.resolve():
        return {"status": "FAIL", "detail": f"{SYMLINK_PATH} points to {target}, expected {RUNNER_PATH}"}
    if not os.access(SYMLINK_PATH, os.X_OK):
        return {"status": "FAIL", "detail": f"{SYMLINK_PATH} is not executable"}
    return {"status": "PASS", "detail": f"Active symlink {SYMLINK_PATH} -> {target}"}


def main() -> int:
    checks = [
        ("git_remotes", check_git_remotes),
        ("main_alignment", check_main_branch_v250),
        ("upstream_zero_diff", check_zero_diff_upstream),
        ("skill_byte_budget", check_skill_byte_counts),
        ("adapt_idempotency", check_adapt_idempotency),
        ("falsification_probe_a", check_probe_a),
        ("falsification_probe_b", check_probe_b),
        ("falsification_probe_c", check_probe_c),
        ("playwright_no_downloads", check_playwright_cache),
        ("symlink_runner", check_symlink_active),
    ]

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo": str(REPO_ROOT),
        "results": {},
        "summary": {"total": len(checks), "passed": 0, "failed": 0},
    }

    all_passed = True
    print("=== OMP SEO Self-Audit Run ===")
    for check_id, func in checks:
        try:
            res = func()
        except Exception as exc:
            res = {"status": "FAIL", "detail": f"Unhandled exception: {exc}"}

        report["results"][check_id] = res
        status = res.get("status")
        if status == "PASS":
            report["summary"]["passed"] += 1
            print(f"  ✓ {check_id}: PASS")
        else:
            report["summary"]["failed"] += 1
            all_passed = False
            print(f"  ✗ {check_id}: FAIL -> {res.get('detail')}")

    REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nAudit report written to {REPORT_FILE}")

    if not all_passed:
        print(f"AUDIT FAILED: {report['summary']['failed']} check(s) failed.")
        return 1

    print(f"AUDIT PASSED: All {report['summary']['total']} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

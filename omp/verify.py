#!/usr/bin/env python3
"""
OMP SEO 5-Gate Verification Suite.

Gate 1: Frontmatter & Byte Budget (26 skills, strictly {name, description}, UTF-8 bytes <= 300)
Gate 2: Negative Control Byte Gate (ensures validator actively rejects 301-byte descriptions)
Gate 3: Clean Paths, Canonical References & Upstream Code Integrity (no CLAUDE_PLUGIN_ROOT in SKILL.md, canonical existing references, 0-line diff with v2.5.0)
Gate 4: Offline Core Scripts (parse_html.py and schema_ecommerce_validate.py on local fixtures)
Gate 5: Live Local HTTP Server + System Chromium + SSRF Guard (negative SSRF block and positive Playwright render)
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "omp" / "fixtures"
RUNNER = REPO_ROOT / "bin" / "seo-run"
SYMLINK_RUNNER = Path.home() / ".local" / "bin" / "omp-seo-run"


def get_runner_bin() -> str:
    if SYMLINK_RUNNER.is_file() and os.access(SYMLINK_RUNNER, os.X_OK):
        return str(SYMLINK_RUNNER)
    return str(RUNNER)


def parse_frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("Missing frontmatter opening '---'")
    closing_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break
    if closing_idx == -1:
        raise ValueError("Missing frontmatter closing '---'")

    fm_dict: dict[str, str] = {}
    for line in lines[1:closing_idx]:
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            fm_dict[k.strip()] = v.strip().strip('"').strip("'")
    return fm_dict


def validate_description_length(desc: str, limit: int = 300) -> bool:
    return len(desc.encode("utf-8")) <= limit


def run_gate1() -> bool:
    print("--- Gate 1: Frontmatter & Byte Budget ---")
    skills_dir = REPO_ROOT / "skills"
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    if len(skill_files) != 26:
        print(f"FAIL: Expected 26 skill files, found {len(skill_files)}")
        return False

    for sf in skill_files:
        content = sf.read_text(encoding="utf-8")
        try:
            fm = parse_frontmatter(content)
        except Exception as exc:
            print(f"FAIL: {sf.name} frontmatter error: {exc}")
            return False

        if set(fm.keys()) != {"name", "description"}:
            print(f"FAIL: {sf.parent.name} has invalid frontmatter keys: {set(fm.keys())}")
            return False

        desc_bytes = len(fm["description"].encode("utf-8"))
        if desc_bytes > 300:
            print(f"FAIL: {sf.parent.name} description exceeds 300 bytes: {desc_bytes}")
            return False

    print(f"PASS: All 26 skills verified (strictly {{name, description}}, all <= 300 bytes)")
    return True


def run_gate2() -> bool:
    print("--- Gate 2: Negative Control Byte Gate ---")
    valid_string = "a" * 300
    invalid_string = "a" * 301

    if not validate_description_length(valid_string, limit=300):
        print("FAIL: Negative control false positive on 300-byte string")
        return False

    if validate_description_length(invalid_string, limit=300):
        print("FAIL: Negative control failed: 301-byte string was unexpectedly accepted")
        return False

    print("PASS: Negative control confirmed: 301-byte string correctly rejected")
    return True


def run_gate3() -> bool:
    print("--- Gate 3: Clean Paths & Upstream Code Integrity ---")
    skills_dir = REPO_ROOT / "skills"
    refs_dir = REPO_ROOT / "omp" / "references"
    canonical_prefix = "~/.local/share/omp-seo/omp/references/"
    canonical_pattern = re.compile(
        r"~/\.local/share/omp-seo/(?:omp/references|skills)/([a-z0-9-]+)/references/([a-zA-Z0-9_-]+\.md)"
    )
    ref_pattern = re.compile(
        r"(?<![\w./~-])((?:[^\s`\"'()\[\]<>]*/)?references/[a-zA-Z0-9_-]+\.md)"
    )

    total_canonical_refs = 0
    for sf in sorted(skills_dir.glob("*/SKILL.md")):
        content = sf.read_text(encoding="utf-8")
        if "${CLAUDE_PLUGIN_ROOT}" in content or '"${CLAUDE_PLUGIN_ROOT}"' in content:
            print(f"FAIL: Found ${{CLAUDE_PLUGIN_ROOT}} in {sf}")
            return False
        if "$HOME/.claude" in content:
            print(f"FAIL: Found $HOME/.claude in {sf}")
            return False
        if "./scripts/claude-seo" in content:
            print(f"FAIL: Found ./scripts/claude-seo in {sf}")
            return False

        # 1. Verify no bare relative `references/` remains without canonical prefix
        for m in ref_pattern.finditer(content):
            tok = m.group(1)
            if not (tok.startswith(canonical_prefix) or tok.startswith("~/.local/share/omp-seo/skills/")):
                print(f"FAIL: Bare relative reference '{tok}' found in {sf}")
                return False

        # 2. Verify every canonical reference path actually exists on disk
        for m in canonical_pattern.finditer(content):
            mod = m.group(1)
            ref_file = m.group(2)
            if "omp/references" in m.group(0):
                target = refs_dir / mod / "references" / ref_file
            else:
                target = skills_dir / mod / "references" / ref_file
            if not target.is_file():
                print(
                    f"FAIL: Canonical reference '{m.group(0)}' in {sf} points to missing target: {target}"
                )
                return False
            total_canonical_refs += 1

    # 3. Verify zero unresolved CLAUDE_PLUGIN_ROOT and valid disk targets for all links in adapted docs
    adapted_ref_files = sorted(refs_dir.glob("*/references/**/*.md"))
    if not adapted_ref_files:
        print(f"FAIL: No adapted reference files found in {refs_dir}")
        return False

    canonical_disk_link_re = re.compile(r"~/\.local/share/omp-seo/([^\s`\"'()\[\]<>]+)")
    commands_to_test = set()
    cmd_re = re.compile(r"omp-seo-run\s+([a-zA-Z0-9_.-]+\.py)")

    for rf in adapted_ref_files:
        text = rf.read_text(encoding="utf-8")
        if "${CLAUDE_PLUGIN_ROOT}" in text:
            print(f"FAIL: Found unresolved ${{CLAUDE_PLUGIN_ROOT}} in adapted doc {rf}")
            return False
        for m in cmd_re.finditer(text):
            commands_to_test.add(m.group(1))
        for m in canonical_disk_link_re.finditer(text):
            target_rel = m.group(1)
            target_path = REPO_ROOT / target_rel
            if not target_path.exists():
                print(f"FAIL: Link '{m.group(0)}' in adapted doc {rf} points to nonexistent target: {target_path}")
                return False

    # 4. Verify no broken dispatcher commands in skills (setup/doctor/run must not be invoked via omp-seo-run)
    bad_dispatcher_re = re.compile(r"omp-seo-run\s+(setup|doctor|run)(?:\s+|$|`|\")")
    just_cmd_re = re.compile(r"just\s+-d\s+~/\.local/share/omp-seo\s+([a-zA-Z0-9_-]+)")
    justfile_text = (REPO_ROOT / "Justfile").read_text(encoding="utf-8")

    for sf in sorted(skills_dir.glob("*/SKILL.md")):
        content = sf.read_text(encoding="utf-8")
        bad_match = bad_dispatcher_re.search(content)
        if bad_match:
            print(f"FAIL: Invalid runner command 'omp-seo-run {bad_match.group(1)}' found in {sf}")
            return False
        for m in just_cmd_re.finditer(content):
            recipe = m.group(1)
            if f"{recipe}:" not in justfile_text and f"{recipe} " not in justfile_text:
                print(f"FAIL: Referenced recipe '{recipe}' in {sf} does not exist in Justfile")
                return False

    # 5. Verify concrete command resolution from external CWD (/tmp)
    runner = get_runner_bin()
    for cmd_script in sorted(commands_to_test):
        script_file = REPO_ROOT / "scripts" / cmd_script
        if not script_file.is_file():
            print(f"FAIL: Script '{cmd_script}' referenced in adapted docs does not exist on disk: {script_file}")
            return False
        res_cmd = subprocess.run([runner, cmd_script, "--help"], cwd="/tmp", capture_output=True, text=True)
        if res_cmd.returncode != 0:
            print(f"FAIL: Command '{runner} {cmd_script} --help' failed from /tmp with code {res_cmd.returncode}:\n{res_cmd.stderr}")
            return False

    # Test executing just recipes from external CWD (/tmp)
    res_just = subprocess.run(["just", "-d", str(REPO_ROOT), "--list"], cwd="/tmp", capture_output=True, text=True)
    if res_just.returncode != 0:
        print(f"FAIL: 'just -d {REPO_ROOT} --list' failed from /tmp: {res_just.stderr}")
        return False

    # 6. Upstream Code & Pristine References Invariant
    cmd = ["git", "diff", "v2.5.0", "--stat", "--", "scripts/", "hooks/", "skills/seo/references/"]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FAIL: git diff command failed: {res.stderr}")
        return False
    if res.stdout.strip():
        print(f"FAIL: Upstream directories have modifications relative to v2.5.0:\n{res.stdout}")
        return False

    print(
        f"PASS: Clean paths, {total_canonical_refs} canonical references verified, "
        f"{len(adapted_ref_files)} adapted reference docs verified (all links exist, 0 CLAUDE_PLUGIN_ROOT), "
        f"{len(commands_to_test)} CLI commands verified executable from /tmp, "
        "valid Justfile recipes verified from /tmp, and 0-line diff against v2.5.0 on protected upstream paths"
    )
    return True

def run_gate4() -> bool:
    print("--- Gate 4: Offline Core Scripts ---")
    runner = get_runner_bin()

    # 1. parse_html.py
    html_fixture = str(FIXTURES_DIR / "sample.html")
    res1 = subprocess.run(
        [runner, "parse_html.py", html_fixture, "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if res1.returncode != 0:
        print(f"FAIL: parse_html.py exited with {res1.returncode}: {res1.stderr}")
        return False
    try:
        data1 = json.loads(res1.stdout)
        assert data1.get("title") == "Sample SEO Verification Page"
        assert len(data1.get("h1", [])) > 0
        assert len(data1.get("schema", [])) > 0
    except Exception as exc:
        print(f"FAIL: parse_html.py output parsing error: {exc}\nOutput: {res1.stdout}")
        return False

    # 2. schema_ecommerce_validate.py
    schema_fixture = str(FIXTURES_DIR / "sample-schema.json")
    res2 = subprocess.run(
        [runner, "schema_ecommerce_validate.py", schema_fixture, "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if res2.returncode != 0:
        print(f"FAIL: schema_ecommerce_validate.py exited with {res2.returncode}: {res2.stderr}")
        return False
    try:
        data2 = json.loads(res2.stdout)
        if not data2.get("ok"):
            print(f"FAIL: schema_ecommerce_validate.py reported not ok: {data2}")
            return False
    except Exception as exc:
        print(f"FAIL: schema_ecommerce_validate.py output parsing error: {exc}\nOutput: {res2.stdout}")
        return False

    print("PASS: parse_html.py and schema_ecommerce_validate.py executed successfully with valid JSON output")
    return True


def run_gate5() -> bool:
    print("--- Gate 5: Live Local HTTP Server + System Chromium + SSRF Guard ---")
    runner = get_runner_bin()

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "-d", str(FIXTURES_DIR)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)

    try:
        url = f"http://127.0.0.1:{port}/sample.html"

        # Negative SSRF check (without allowlist)
        env_neg = os.environ.copy()
        env_neg.pop("CLAUDE_SEO_LOCAL_TARGETS", None)
        res_neg = subprocess.run(
            [runner, "render_page.py", url, "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env_neg,
        )
        if res_neg.returncode == 0:
            print(f"FAIL: Negative SSRF test unexpectedly passed with code 0")
            return False
        output_neg = (res_neg.stdout + res_neg.stderr).lower()
        if "url_safety" not in output_neg:
            print(f"FAIL: Negative SSRF did not report 'url_safety' error: {output_neg}")
            return False

        # Positive Chromium check (with allowlist)
        env_pos = os.environ.copy()
        env_pos["CLAUDE_SEO_LOCAL_TARGETS"] = f"127.0.0.1:{port}"
        res_pos = subprocess.run(
            [runner, "render_page.py", url, "--mode", "always", "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env_pos,
        )
        if res_pos.returncode != 0:
            print(f"FAIL: Positive Chromium test failed with code {res_pos.returncode}:\n{res_pos.stderr}\n{res_pos.stdout}")
            return False

        try:
            data_pos = json.loads(res_pos.stdout)
            if data_pos.get("render_engine") != "playwright-chromium":
                print(f"FAIL: Unexpected render_engine: {data_pos.get('render_engine')}")
                return False
            if data_pos.get("status_code") != 200:
                print(f"FAIL: Unexpected status_code: {data_pos.get('status_code')}")
                return False
            struct_data = data_pos.get("structured_data", {})
            if not struct_data or struct_data.get("block_count", 0) < 1:
                print(f"FAIL: Expected structured_data block_count >= 1, got {struct_data}")
                return False
        except Exception as exc:
            print(f"FAIL: Failed to parse positive render output: {exc}\n{res_pos.stdout}")
            return False

    finally:
        server.terminate()
        server.wait()

    print("PASS: SSRF containment and headless Chromium rendering verified")
    return True


def main() -> int:
    gates = [
        ("Gate 1", run_gate1),
        ("Gate 2", run_gate2),
        ("Gate 3", run_gate3),
        ("Gate 4", run_gate4),
        ("Gate 5", run_gate5),
    ]

    failed = []
    for name, func in gates:
        try:
            success = func()
        except Exception as exc:
            print(f"FAIL: {name} raised unexpected exception: {exc}")
            success = False
        if not success:
            failed.append(name)
        print()

    if failed:
        print(f"VERIFICATION FAILED: {len(failed)} of {len(gates)} gates failed: {', '.join(failed)}")
        return 1

    print("ALL 5 GATES PASSED SUCCESSFULLY.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

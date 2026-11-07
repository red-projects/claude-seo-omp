#!/usr/bin/env python3
"""
OMP SEO 5-Gate Verification Suite.

Gate 1: Frontmatter & Byte Budget (26 skills, strictly {name, description}, UTF-8 bytes <= 300)
Gate 2: Negative Control Byte Gate (ensures validator actively rejects 301-byte descriptions)
Gate 3: Clean Paths & Upstream Code Integrity (no CLAUDE_PLUGIN_ROOT in SKILL.md, 0-line diff with v2.5.0)
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
    for sf in sorted(skills_dir.glob("*/SKILL.md")):
        content = sf.read_text(encoding="utf-8")
        if "${CLAUDE_PLUGIN_ROOT}" in content or '"${CLAUDE_PLUGIN_ROOT}"' in content:
            print(f"FAIL: Found ${{CLAUDE_PLUGIN_ROOT}} in {sf}")
            return False

    cmd = ["git", "diff", "v2.5.0", "--stat", "--", "scripts/", "hooks/", "skills/seo/references/"]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FAIL: git diff command failed: {res.stderr}")
        return False
    if res.stdout.strip():
        print(f"FAIL: Upstream directories have modifications relative to v2.5.0:\n{res.stdout}")
        return False

    print("PASS: No ${CLAUDE_PLUGIN_ROOT} in SKILL.md and 0-line diff against v2.5.0 on protected upstream paths")
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

# OMP SEO Adaptation & Operator Guide

This document describes the OMP (Omarchy / OMP Multi-Agent Platform) adaptation layer for `claude-seo`.

- **Upstream Repository:** [AgriciDaniel/claude-seo](https://github.com/AgriciDaniel/claude-seo) (`v2.5.0`)
- **Downstream Fork:** [red-projects/claude-seo-omp](https://github.com/red-projects/claude-seo-omp)
- **Active Branch:** `omp-compat`

---

## 1. Overview & Architecture

`claude-seo-omp` adapts the comprehensive 26-skill suite from `claude-seo` into OMP's multi-agent architecture while preserving 100% upstream script and reference integrity.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        OMP Orchestrator Session                        │
│                  (0 bytes SEO context / 0 token bloat)                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ task(agent: "seo", ...)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 Isolated SEO Subagent (~/.omp/agent/agents/seo.md)     │
│                 Model Role: @seo                                       │
│                                                                        │
│   • On-demand skill lookup: skills/<name>/SKILL.md                     │
│   • Reference files: skills/seo/references/*.md                        │
│   • CLI dispatch: omp-seo-run <script.py> [args]                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
           ┌────────────────────────┴────────────────────────┐
           ▼                                                 ▼
┌───────────────────────────────┐         ┌──────────────────────────────┐
│       OMP Runtime Shim        │         │      Upstream Engine         │
│  • bin/seo-run                │         │  • scripts/*.py (0 diff)     │
│  • omp/sitecustomize.py       │ ──────> │  • hooks/* (0 diff)          │
│  • /usr/bin/chromium          │         │  • skills/seo/references/    │
│  • SSRF Local Target Hook     │         │    (0 diff)                  │
└───────────────────────────────┘         └──────────────────────────────┘
```

### Architectural Principles

1. **0-Byte Orchestrator Context Bloat**
   - Standard upstream installations inject extensive skill catalogs directly into the primary assistant prompt, consuming valuable context tokens on every turn.
   - OMP encapsulates all SEO capabilities into a dedicated, isolated subagent definition at `~/.omp/agent/agents/seo.md` using model role `@seo`.
   - The primary orchestrator context retains 0 bytes of SEO skill overhead. SEO tasks are delegated on-demand via subagents (`task(agent: "seo", ...)`).

2. **Zero-Conflict Upstream Tracking**
   - Upstream core files have **0 lines of diff** against `v2.5.0`:
     - `scripts/` (0 lines changed)
     - `hooks/` (0 lines changed)
     - `skills/seo/references/` (0 lines changed)
   - All OMP-specific compatibility logic is isolated inside `omp/`, `bin/`, and `Justfile`.
   - Future upstream releases and security fixes can be merged cleanly with minimal risk of merge conflicts.

3. **System Chromium & Offline Runtime**
   - Upstream Playwright triggers automated downloads of bundled browser binaries (~300 MB), which can fail in air-gapped or restricted environments.
   - OMP hooks Playwright launch via `omp/sitecustomize.py`, routing calls to system `/usr/bin/chromium` with flags `--no-sandbox --disable-gpu --disable-dev-shm-usage`.
   - `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` prevents redundant network fetches.
   - The `url_safety` hook safely allows local development targets (e.g. `localhost`, `127.0.0.1`, `.ddev.site`) when auditing local sites without relaxing external SSRF guards.

4. **Deterministic Frontmatter Normalization**
   - Upstream skills use Claude Code specific frontmatter schemas and absolute plugin variables (`${CLAUDE_PLUGIN_ROOT}`).
   - `omp/adapt.py` normalizes all 26 skills (`skills/*/SKILL.md`) to strict OMP specification:
     - Strict frontmatter: `{name, description}` where description is $\le 300$ bytes UTF-8.
     - Strips Claude Code installer preambles and rewrites launcher paths to canonical `omp-seo-run`.
     - Operation is fully idempotent (`adapt.py` re-runs yield 0 diff).

---

## 2. Command Reference (`Justfile`)

All common operations are automated via project recipes in `Justfile`:

| Recipe | Description | Command Executed |
|---|---|---|
| `just setup` | Create isolated `.venv`, install core dependencies (`omp/requirements-core.txt`), symlink `~/.local/bin/omp-seo-run`. | `python3 -m venv .venv && pip install -r omp/requirements-core.txt && ln -sf $(pwd)/bin/seo-run ~/.local/bin/omp-seo-run` |
| `just setup-full` | Optional full setup including upstream extended packages from `requirements.txt`. | `just setup && pip install -r requirements.txt` |
| `just adapt` | Run deterministic frontmatter and path normalizer across all 26 skill definitions. | `python3 omp/adapt.py` |
| `just test` | Run the 5-gate verification suite (byte budgets, negative controls, zero diff, offline scripts, headless rendering). | `python3 omp/verify.py` |
| `just self-audit` | Run 10-check audit suite with 3 falsification probes; outputs `omp/audit-report.json`. | `python3 omp/self_audit.py` |
| `just sync-upstream <tag>` | Fetch upstream git tags, merge target `<tag>`, run `adapt`, `test`, and `self-audit`. | `git fetch upstream --tags && git merge {{tag}} && python3 omp/adapt.py ...` |
| `just link-core <project-dir>` | Symlink 6 core skills into `<project-dir>/.omp/skills` (or `--claude`). | `bash omp/link_project.sh core <project_dir>` |
| `just link-all <project-dir>` | Symlink all 26 skills into `<project-dir>/.omp/skills` (or `--claude`). | `bash omp/link_project.sh all <project_dir>` |

### Verification Gates (`just test`)

The test suite validates 5 critical gates:
- **Gate 1:** Frontmatter & Byte Budget — strictly `{name, description}`, $\le 300$ bytes for all 26 skills.
- **Gate 2:** Negative Control — confirms validation fails when frontmatter exceeds 300 bytes.
- **Gate 3:** Clean Paths & Integrity — no `${CLAUDE_PLUGIN_ROOT}` in skills; 0 diff in upstream scripts/hooks.
- **Gate 4:** Offline Scripts — confirms `parse_html.py` and `schema_ecommerce_validate.py` execute and return valid JSON.
- **Gate 5:** Live Local HTTP & Headless Chromium — tests live SSRF protection, loopback navigation, and system Chromium rendering.

---

## 3. Agent & Operator Usage

### A. Calling from OMP Multi-Agent Sessions

To invoke SEO analysis within OMP without bloating the main orchestrator, spawn a delegated task:

```json
{
  "context": "Conducting on-page technical and structured data review for staging release",
  "tasks": [
    {
      "agent": "seo",
      "task": "Audit https://staging.example.com for Core Web Vitals, Schema.org JSON-LD compliance, and llms.txt readiness.",
      "solutionSpace": "technical SEO audit and schema validation"
    }
  ]
}
```

The `@seo` subagent will:
1. Consult relevant skills on-demand from `/home/konstantin/.local/share/omp-seo/skills/<name>/SKILL.md`.
2. Inspect standard thresholds (`skills/seo/references/cwv-thresholds.md`, `schema-types.md`, `eeat-framework.md`).
3. Run necessary CLI analysis tools via `omp-seo-run`.
4. Synthesize findings under the `PERCEIVE -> ANALYZE -> VALIDATE -> ACT` pipeline.

### B. Direct CLI Usage via `omp-seo-run`

`omp-seo-run` is symlinked to `~/.local/bin/omp-seo-run` during `just setup`. It wraps execution inside the isolated `.venv` with system Chromium and PYTHONPATH configured.

#### Script Listing
```bash
omp-seo-run --list
```

#### Common Commands

1. **Parse HTML On-Page Elements:**
   ```bash
   omp-seo-run parse_html.py https://example.com --json
   # Or parse local file:
   omp-seo-run parse_html.py /path/to/page.html --json
   ```

2. **Render Page with Headless Chromium (SPA / JS-rendered content):**
   ```bash
   omp-seo-run render_page.py https://example.com --mode auto --json
   ```

3. **AI Search & Agent Readiness Check (llms.txt, WebMCP, AI crawlers):**
   ```bash
   omp-seo-run agentic_check.py https://example.com
   ```

4. **Validate Schema.org Microdata / JSON-LD:**
   ```bash
   omp-seo-run schema_ecommerce_validate.py /path/to/schema.json
   ```

5. **XML Sitemap Discovery & Validation:**
   ```bash
   omp-seo-run sitemap_discovery.py https://example.com
   ```

6. **Auditing Local / Staging Targets:**
   By default, `url_safety.py` restricts access to loopback and private IP addresses. For local test servers, whitelist the target host/port:
   ```bash
   CLAUDE_SEO_LOCAL_TARGETS="localhost:8080,127.0.0.1:8080" \
     omp-seo-run render_page.py http://localhost:8080/ --json
   ```

---

## 4. Repository Structure

```
/home/konstantin/.local/share/omp-seo/
├── Justfile                      # Operator task automation
├── README-OMP.md                 # This documentation file
├── bin/
│   └── seo-run                   # Isolated CLI dispatcher
├── omp/
│   ├── adapt.py                  # Frontmatter & launcher normalizer
│   ├── link_project.sh           # Workspace skill symlinker (.omp/skills)
│   ├── requirements-core.txt     # Pinned Python requirements
│   ├── self_audit.py             # 10-check audit with falsification probes
│   ├── sitecustomize.py          # System Chromium & SSRF transparent hook
│   ├── verify.py                 # 5-gate test suite
│   └── fixtures/                 # Test HTML, schema, and llms.txt fixtures
├── skills/                       # 26 adapted skill specifications
│   ├── seo/                      # Core SEO skill & reference library
│   ├── seo-agentic/              # AI crawler & llms.txt audit
│   ├── seo-audit/                # Multi-dimensional audit orchestrator
│   └── ...                       # 23 additional specialized skills
├── scripts/                      # Upstream Python engines (0 diff)
└── hooks/                        # Upstream hooks (0 diff)
```

---

## 5. Maintenance & Upstream Upgrades

When a new release tag or commits are published on `AgriciDaniel/claude-seo`:

```bash
cd ~/.local/share/omp-seo

# 1. Transactional upstream sync (fetches upstream, merges with --no-ff --no-commit,
#    runs adapt/verify/self-audit, and stages adaptation delta if all pass)
just sync-upstream v2.6.0   # or branch: just sync-upstream main

# 2. Inspect the staged merge and adaptation delta
git diff --cached

# 3. Complete the merge explicitly (never auto-committed or auto-pushed)
git commit -m "chore: sync with upstream v2.6.0"
git push origin omp-compat

# Alternatively, to cleanly abandon an in-progress or failed sync:
git merge --abort
```

### Transactional Sync Contract
- **Preflight:** Refuses to run on dirty tracked trees (exit code 2) or if `MERGE_HEAD` already exists.
- **Transactional Merge:** Merges using `git merge --no-ff --no-commit`. HEAD is never moved prematurely.
- **Worktree Compatibility:** Uses `git rev-parse --git-path MERGE_HEAD` supporting both standard checkouts and git worktrees.
- **Untracked Collision Safety:** If merge fails due to an untracked file collision, exits 2 without creating a bogus merge state or advising abort.
- **Post-Merge Adaptation & Validation:** Automatically runs `omp/adapt.py`, `omp/verify.py` (5 gates), and `omp/self_audit.py` (11 checks) while merge is staged.
- **Failure Containment:** If any check fails, exits non-zero (exit code 4) leaving `MERGE_HEAD` intact so the operator can inspect or run `git merge --abort`.
- **Staging Delta:** On success, runs `git add -u` staging only tracked adaptation changes so the index matches the worktree (`git diff --quiet`), leaving user untracked files unstaged.
- **Operator Authority:** Never auto-commits and never pushes.

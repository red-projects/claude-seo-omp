set shell := ["bash", "-uc"]

default:
    @just --list

# Set up isolated virtual environment with core OMP requirements
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r omp/requirements-core.txt
    chmod +x bin/seo-run omp/link_project.sh
    mkdir -p ~/.local/bin
    ln -sf "$(pwd)/bin/seo-run" ~/.local/bin/omp-seo-run
    echo "✓ OMP SEO runtime setup complete."

# Full setup including upstream dev/extended dependencies
setup-full: setup
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f requirements.txt ]; then
        .venv/bin/pip install -r requirements.txt || true
    fi
    echo "✓ Full setup complete."

# Adapt skill files for OMP compatibility (deterministic, idempotent)
adapt:
    python3 omp/adapt.py

# Run the 5-gate verification suite
test:
    python3 omp/verify.py

# Run self-audit with falsification probes and generate audit report
self-audit:
    python3 omp/self_audit.py

# Safely fetch upstream tag or branch, merge into current branch, adapt and verify
sync-upstream tag="v2.5.0":
    bash omp/sync-upstream.sh "{{tag}}"

# Link core skills into target project
link-core project_dir:
    bash omp/link_project.sh core "{{project_dir}}"

# Link all 26 skills into target project
link-all project_dir:
    bash omp/link_project.sh all "{{project_dir}}"

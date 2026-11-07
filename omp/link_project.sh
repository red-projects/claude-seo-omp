#!/usr/bin/env bash
set -euo pipefail

# Link OMP SEO skills into a target project workspace
# Usage:
#   bash omp/link_project.sh core <project_dir>
#   bash omp/link_project.sh all <project_dir>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <core|all> <project_dir>" >&2
    exit 1
fi

MODE="$1"
PROJECT_DIR="$(readlink -f "$2")"

if [[ ! -d "${PROJECT_DIR}" ]]; then
    echo "Error: target project directory does not exist: ${PROJECT_DIR}" >&2
    exit 1
fi

TARGET_SKILLS_DIR="${PROJECT_DIR}/.claude/skills"
mkdir -p "${TARGET_SKILLS_DIR}"

CORE_SKILLS=(
    "seo"
    "seo-audit"
    "seo-technical"
    "seo-content"
    "seo-schema"
    "seo-plan"
)

if [[ "${MODE}" == "core" ]]; then
    SKILLS_TO_LINK=("${CORE_SKILLS[@]}")
elif [[ "${MODE}" == "all" ]]; then
    SKILLS_TO_LINK=()
    for d in "${REPO_ROOT}/skills"/*/; do
        if [[ -f "${d}SKILL.md" ]]; then
            SKILLS_TO_LINK+=("$(basename "${d}")")
        fi
    done
else
    echo "Error: unknown mode '${MODE}'. Use 'core' or 'all'." >&2
    exit 1
fi

echo "Linking ${#SKILLS_TO_LINK[@]} skill(s) [mode: ${MODE}] into ${TARGET_SKILLS_DIR}..."
for skill in "${SKILLS_TO_LINK[@]}"; do
    SRC="${REPO_ROOT}/skills/${skill}"
    DEST="${TARGET_SKILLS_DIR}/${skill}"
    if [[ -d "${SRC}" ]]; then
        ln -sfn "${SRC}" "${DEST}"
        echo "  ✓ linked ${skill}"
    else
        echo "  ✗ skill source not found: ${SRC}" >&2
    fi
done

echo "Done."

#!/usr/bin/env python3
"""
Deterministic Skill Adapter for OMP.

Adapts skills/*/SKILL.md frontmatter and launcher paths for OMP runtime compatibility:
- Replaces frontmatter with strictly {name, description} (<=300 bytes UTF-8).
- Rewrites Claude Code launcher references and preambles to canonical OMP `omp-seo-run`.
- Cleans installer and manual path notes ($HOME/.claude, ./scripts/claude-seo).
- Rewrites every `references/<file>.md` link to an absolute canonical skill path.
- Replaces Claude Code subagent delegation with inline specialist-module execution.
- Fully idempotent: running multiple times produces 0 changes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKILL_DESCRIPTIONS = {
    "seo": "Комплексный SEO-аудит сайта: технические параметры, Schema.org, E-E-A-T, AI-видимость (GEO) и Core Web Vitals.",
    "seo-agentic": "Оценка готовности сайта к AI-агентам: аудит llms.txt, разметка WebMCP, доступность контента для браузерных LLM.",
    "seo-audit": "Полный аудит сайта: параллельный анализ технического SEO, контента, разметки, скорости и автогенерация плана работ.",
    "seo-backlinks": "Анализ ссылочного профиля сайта: аудит доноров, анкор-листа, токсичных ссылок и оценка авторитетности домена.",
    "seo-cluster": "Кластеризация семантического ядра и поисковых запросов: группировка по интентам и структурирование разделов.",
    "seo-competitor-pages": "Сравнительный анализ страниц конкурентов: сопоставление контента, структуры, мета-тегов и ключевых слов.",
    "seo-content-brief": "Генерация технического задания (Brief) на SEO-копирайтинг: структура H1-H3, LSI-слова, интент и требования E-E-A-T.",
    "seo-content": "Анализ качества текстового контента: проверка соответствия факторам Google E-E-A-T, плотности ключей и уникальности.",
    "seo-dataforseo": "Интеграция с API DataForSEO: получение данных поисковой выдачи, стоимости клика, частотности и бэклинков.",
    "seo-drift": "Мониторинг изменений поисковой выдачи и позиций сайта (SERP drift) относительно контрольного снимка.",
    "seo-ecommerce": "SEO-аудит интернет-магазинов: каталоги, пагинация, фильтры, карточки товаров, микроразметка Product и Offer.",
    "seo-flow": "Оптимизация пути пользователя и конверсионных воронок с учетом поискового интента и органического трафика.",
    "seo-geo": "Оптимизация под AI-поисковики (GEO/AEO): цитируемость в ChatGPT, Perplexity, Google AI Overviews и Gemini.",
    "seo-google": "Интеграция с сервисами Google: анализ метрик Google Search Console, Core Web Vitals (CrUX) и Google Analytics 4.",
    "seo-hreflang": "Аудит мультиязычности и региональности: проверка тегов hreflang, x-default, локализованных URL и sitemap.",
    "seo-image-gen": "Генерация промптов и оптимизация изображений для SEO: атрибуты alt, title, сжатие и семантический контекст.",
    "seo-images": "Техническая оптимизация изображений: форматы WebP/AVIF, сжатие, responsive srcset, lazy-loading и LCP-аудит.",
    "seo-local": "Локальное SEO: оптимизация профиля компании (GBP), NAP-данные, гео-запросы, отзывы и локальные посадочные.",
    "seo-maps": "Аудит видимости на онлайн-картах: гео-ранжирование, радиус присутствия, категории карточки и репутация.",
    "seo-page": "Глубокий постраничный on-page SEO аудит одного URL: мета-теги, заголовки, разметка, скорость и контент.",
    "seo-plan": "Формирование стратегического плана поискового продвижения с приоритизацией задач по матрице Impact / Effort.",
    "seo-programmatic": "Проектирование и аудит шаблонных страниц (Programmatic SEO): шаблоны мета-тегов, защита от дублей и каннибализации.",
    "seo-schema": "Валидация и генерация микроразметки Schema.org (JSON-LD): Article, Product, FAQ, LocalBusiness, Breadcrumbs.",
    "seo-sitemap": "Аудит и валидация XML-карт сайта: проверка доступности URL, соответствия sitemap-index, даты lastmod и лимитов.",
    "seo-sxo": "Search Experience Optimization: объединение поисковой оптимизации и удобства интерфейса (UX, микроконверсии, читаемость).",
    "seo-technical": "Глубокий технический аудит: краулинг, индексация, robots.txt, HTTP-коды, каноникалы, редиректы, JS-рендеринг."
}


CANONICAL_SKILLS_ROOT = "~/.local/share/omp-seo/skills"
CANONICAL_REFS_ROOT = "~/.local/share/omp-seo/omp/references"

def canonicalize_runtime_refs(content: str) -> str:
    """Canonicalize command invocations and paths to OMP runtime standards."""
    # 1. Map setup and doctor to actual Justfile recipes
    content = content.replace('"${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo" setup', "just -d ~/.local/share/omp-seo setup")
    content = content.replace('${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo setup', "just -d ~/.local/share/omp-seo setup")
    content = content.replace('"${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo" doctor --json', "just -d ~/.local/share/omp-seo test")
    content = content.replace('${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo doctor --json', "just -d ~/.local/share/omp-seo test")
    content = content.replace('"${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo" doctor', "just -d ~/.local/share/omp-seo test")
    content = content.replace('${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo doctor', "just -d ~/.local/share/omp-seo test")
    content = content.replace('"${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo" run ', "omp-seo-run ")
    content = content.replace("${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo run ", "omp-seo-run ")
    content = content.replace('"${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo" run', "omp-seo-run")
    content = content.replace("${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo run", "omp-seo-run")
    content = content.replace('"${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo"', "omp-seo-run")
    content = content.replace("${CLAUDE_PLUGIN_ROOT}/scripts/claude-seo", "omp-seo-run")

    # Clean up any preexisting artifacts in working tree
    content = content.replace("`omp-seo-run run`", "`omp-seo-run`")
    content = content.replace("omp-seo-run setup", "just -d ~/.local/share/omp-seo setup")
    content = content.replace("omp-seo-run doctor --json", "just -d ~/.local/share/omp-seo test")
    content = content.replace("omp-seo-run doctor", "just -d ~/.local/share/omp-seo test")
    content = content.replace("omp-seo-run run command", "omp-seo-run command")
    content = content.replace("omp-seo-run run ", "omp-seo-run ")
    content = content.replace("omp-seo-run run", "omp-seo-run")

    # 2. Only map skills/<skill>/references/ to CANONICAL_REFS_ROOT.
    # Non-references paths (e.g. skills/<skill>/SKILL.md) map to CANONICAL_SKILLS_ROOT.
    content = re.sub(
        r"\$\{CLAUDE_PLUGIN_ROOT\}/skills/([a-zA-Z0-9_-]+)/references/",
        rf"{CANONICAL_REFS_ROOT}/\1/references/",
        content,
    )
    content = content.replace("${CLAUDE_PLUGIN_ROOT}/skills/", f"{CANONICAL_SKILLS_ROOT}/")
    content = content.replace("${CLAUDE_PLUGIN_ROOT}", "~/.local/share/omp-seo")
    content = content.replace('"$HOME/.claude/skills/', "omp-seo-run")
    content = content.replace("$HOME/.claude/skills/", "omp-seo-run")
    content = content.replace("./scripts/claude-seo", "omp-seo-run")
    return content


def adapt_references(repo_root: Path) -> int:
    """Generate pristine-preserving adapted copies of reference files under omp/references/."""
    skills_dir = repo_root / "skills"
    refs_out_dir = repo_root / "omp" / "references"
    changed = 0

    for ref_file in sorted(skills_dir.glob("*/references/**/*.md")):
        rel = ref_file.relative_to(skills_dir)
        target = refs_out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)

        original = ref_file.read_text(encoding="utf-8")
        adapted = canonicalize_runtime_refs(original)

        if not target.is_file() or target.read_text(encoding="utf-8") != adapted:
            target.write_text(adapted, encoding="utf-8")
            changed += 1

    return changed


# Match reference links: bare (`references/x.md`), relative (`../seo/references/x.md`),
# or already canonical (`~/.local/share/omp-seo/...`).
REFERENCE_PATTERN = re.compile(
    r"(?<![\w./~-])"
    r"("
    r"(?:[^\s`\"'()\[\]<>]*/)?references/([a-zA-Z0-9_-]+\.md)"
    r")"
)


def rewrite_references(content: str, skill_name: str, repo_root: Path) -> str:
    """Rewrite relative reference links to deterministic canonical paths in omp/references/."""
    skills_dir = repo_root / "skills"
    refs_dir = repo_root / "omp" / "references"

    def repl(m: re.Match) -> str:
        full_tok = m.group(1)
        ref_file = m.group(2)

        # Route to adapted copy under omp/references if present
        if (refs_dir / skill_name / "references" / ref_file).is_file():
            return f"{CANONICAL_REFS_ROOT}/{skill_name}/references/{ref_file}"
        if (refs_dir / "seo" / "references" / ref_file).is_file():
            return f"{CANONICAL_REFS_ROOT}/seo/references/{ref_file}"

        # Fallback to skills_dir
        if (skills_dir / skill_name / "references" / ref_file).is_file():
            return f"{CANONICAL_SKILLS_ROOT}/{skill_name}/references/{ref_file}"
        if (skills_dir / "seo" / "references" / ref_file).is_file():
            return f"{CANONICAL_SKILLS_ROOT}/seo/references/{ref_file}"

        return full_tok

    return REFERENCE_PATTERN.sub(repl, content)

def adapt_skill(skill_file: Path) -> bool:
    name = skill_file.parent.name
    if name not in SKILL_DESCRIPTIONS:
        raise KeyError(f"Skill '{name}' has no defined description in SKILL_DESCRIPTIONS")

    description = SKILL_DESCRIPTIONS[name]
    desc_bytes = len(description.encode("utf-8"))
    assert desc_bytes <= 300, f"Description for {name} exceeds 300 bytes: {desc_bytes}"

    content = skill_file.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{skill_file} does not start with YAML frontmatter delimiter '---'")

    closing_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break

    if closing_idx == -1:
        raise ValueError(f"{skill_file} has unclosed YAML frontmatter")

    # Frontmatter replacement
    new_frontmatter = (
        "---\n"
        f"name: {name}\n"
        f'description: "{description}"\n'
        "---\n"
    )

    body = "".join(lines[closing_idx + 1:])

    # Canonical OMP runtime preamble
    canonical_runtime = (
        "**Runtime:** Run bundled Python tools through `omp-seo-run <script.py>`. "
        "That is the single canonical form used by every skill and agent in OMP. "
        "Never invoke bundled scripts with a bare Python interpreter."
    )
    body = re.sub(
        r"\*\*Runtime:\*\*.*?with a bare Python interpreter\.",
        canonical_runtime,
        body,
        flags=re.DOTALL,
    )

    # Clarify Claude Code subagent delegation phrasing for OMP runtime
    body = re.sub(
        r"(\*\*)?Delegate to subagents(\*\*)?\s*\(if available, otherwise run inline sequentially\):",
        r"\1Execute specialist modules inline sequentially\2 (read ~/.local/share/omp-seo/skills/<module>/SKILL.md as needed):",
        body,
    )

    # Canonical path and invocation replacements
    body = canonicalize_runtime_refs(body)

    # Canonicalize reference links into omp/references/
    repo_root = skill_file.resolve().parent.parent.parent
    body = rewrite_references(body, name, repo_root)

    new_content = new_frontmatter + body

    if new_content != content:
        skill_file.write_text(new_content, encoding="utf-8")
        return True
    return False


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    skills_dir = repo_root / "skills"

    refs_changed = adapt_references(repo_root)

    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    if len(skill_files) != 26:
        print(f"Error: expected 26 skills, found {len(skill_files)}", file=sys.stderr)
        return 1

    changed_count = 0
    for sf in skill_files:
        if adapt_skill(sf):
            changed_count += 1

    ref_count = len(sorted((repo_root / "omp" / "references").glob("*/references/**/*.md")))
    print(f"Adapted {len(skill_files)} skills and {ref_count} references. Changed: {changed_count} skills, {refs_changed} references.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

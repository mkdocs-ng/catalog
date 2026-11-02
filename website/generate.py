"""Generate the catalog website's Markdown pages.

Reads the newest ``history/*_projects.csv`` (written by best-of every week) and
``projects.yaml`` (for categories and the ``ng-tested`` label), and writes one
page per category plus a home page and a "Tested on MkDocs NG" page into
``website/docs/``, next to the files from ``website/static/``. Run it before ``mkdocs build``:

    python website/generate.py && mkdocs build -f website/mkdocs.yml
"""

from __future__ import annotations

import ast
import csv
import html
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml
from markdown.extensions.toc import slugify, unique

ROOT = Path(__file__).resolve().parents[1]
DOCS = Path(__file__).resolve().parent / "docs"
STATIC = Path(__file__).resolve().parent / "static"
COMPAT_URL = "https://github.com/mkdocs-ng/mkdocs/actions/workflows/compat.yml"
CASES_URL = "https://github.com/mkdocs-ng/mkdocs/blob/main/compat/cases.yaml"
INACTIVE_DAYS = 365
MAX_EXTENSIONS = 8

ICONS = {
    "theming": "palette",
    "api-docs": "code",
    "blogging": "pen-line",
    "charts": "chart-column",
    "code-exec-templating": "terminal",
    "git-info": "git-branch",
    "html-css": "paintbrush",
    "integrations": "plug",
    "i18n-l10n": "languages",
    "links-refs": "link",
    "markdown": "puzzle",
    "nav-pages": "compass",
    "quality": "circle-check",
    "search-toc": "search",
    "site-conversion": "file-output",
    "site-management": "wrench",
    "snippets-include": "files",
}


def as_list(value: str) -> list[str]:
    """CSV cells hold either a plain name or a Python list literal."""
    if not value:
        return []
    if value.startswith("["):
        return [str(v) for v in ast.literal_eval(value)]
    return [value]


def as_number(value: str) -> int:
    return int(float(value)) if value else 0


def as_date(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value[:19]).replace(tzinfo=timezone.utc)


def short_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def plain_title(title: str) -> str:
    """Drop the emoji the README uses in front of category titles."""
    first, _, rest = title.partition(" ")
    return rest if rest and not first.isascii() else title


def text(value: str) -> str:
    """Keep descriptions from injecting HTML into the page."""
    return html.escape(" ".join(value.split()), quote=False)


def load():
    config = yaml.safe_load((ROOT / "projects.yaml").read_text(encoding="utf-8"))
    tested = {p["name"] for p in config["projects"] if "ng-tested" in (p.get("labels") or [])}
    categories = {c["category"]: plain_title(c["title"]) for c in config["categories"]}

    csv_path = sorted((ROOT / "history").glob("*_projects.csv"))[-1]
    data_date = csv_path.name.split("_")[0]
    with csv_path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("show", "True") == "True"]

    projects = []
    for row in rows:
        projects.append(
            {
                "name": row["name"],
                "description": text(row.get("description", "")),
                "category": row["category"] if row["category"] in categories else "other",
                "pypi": row.get("pypi_id", ""),
                "github": row.get("github_url", ""),
                "homepage": row.get("homepage", ""),
                "license": row.get("license", ""),
                "theme": row.get("mkdocs_theme", ""),
                "plugins": as_list(row.get("mkdocs_plugin", "")),
                "extensions": as_list(row.get("markdown_extension", "")),
                "stars": as_number(row.get("star_count", "")),
                "downloads": as_number(row.get("monthly_downloads", "")),
                "rank": as_number(row.get("projectrank", "")),
                "last_commit": as_date(row.get("last_commit_pushed_at", "")),
                "tested": row["name"] in tested,
            }
        )
    if any(p["category"] == "other" for p in projects):
        categories["other"] = "Others"
    projects.sort(key=lambda p: (-p["rank"], -p["stars"], p["name"].lower()))
    return categories, projects, data_date


def project_section(p: dict, now: datetime) -> str:
    lines = [f"## {p['name']}", ""]
    if p["description"]:
        lines += [p["description"], ""]

    facts = []
    if p["pypi"]:
        facts.append(f"**Install:** `pip install {p['pypi']}`")
    if p["theme"]:
        facts.append(f"**Theme:** `{p['theme']}`")
    if p["plugins"]:
        label = "Plugin" if len(p["plugins"]) == 1 else "Plugins"
        facts.append(f"**{label}:** " + ", ".join(f"`{n}`" for n in p["plugins"]))
    if p["extensions"]:
        shown = ", ".join(f"`{n}`" for n in p["extensions"][:MAX_EXTENSIONS])
        more = len(p["extensions"]) - MAX_EXTENSIONS
        if more > 0:
            shown += f" and {more} more"
        label = "Markdown extension" if len(p["extensions"]) == 1 else "Markdown extensions"
        facts.append(f"**{label}:** {shown}")
    if p["tested"]:
        facts.append(f":lucide-badge-check: **Builds on MkDocs NG** in the [weekly compatibility test]({COMPAT_URL})")

    stats = []
    if p["stars"]:
        stats.append(f":lucide-star: {short_number(p['stars'])}")
    if p["downloads"]:
        stats.append(f":lucide-download: {short_number(p['downloads'])}/month")
    if p["last_commit"]:
        stats.append(f":lucide-clock: last commit {p['last_commit']:%Y-%m-%d}")
    if p["license"]:
        stats.append(f":lucide-scale: {p['license']}")
    if stats:
        facts.append(" · ".join(stats))

    links = []
    if p["github"]:
        links.append(f"[:brand-github: GitHub]({p['github']})")
    if p["pypi"]:
        links.append(f"[:brand-pypi: PyPI](https://pypi.org/project/{p['pypi']}/)")
    if p["homepage"] and p["homepage"].rstrip("/") != p["github"].rstrip("/"):
        links.append(f"[Homepage]({p['homepage']})")
    if links:
        facts.append(" · ".join(links))

    lines += [f"- {fact}" for fact in facts]
    if p["last_commit"] and (now - p["last_commit"]).days > INACTIVE_DAYS:
        lines += ["", f"> [!WARNING]\n> No commits since {p['last_commit']:%Y-%m-%d}. Check that it still fits your setup."]
    return "\n".join(lines) + "\n"


def write(name: str, content: str) -> None:
    (DOCS / name).write_text(content, encoding="utf-8")


def main() -> None:
    categories, projects, data_date = load()
    now = datetime.now(timezone.utc)
    if DOCS.exists():
        shutil.rmtree(DOCS)
    shutil.copytree(STATIC, DOCS)  # logo, favicon, CSS
    (DOCS / "categories").mkdir()

    by_category = {key: [p for p in projects if p["category"] == key] for key in categories}
    anchors: dict[str, str] = {}

    for key, title in categories.items():
        items = by_category[key]
        if not items:
            continue
        tested = sum(p["tested"] for p in items)
        used: set[str] = set()
        sections = []
        for p in items:
            anchor = unique(slugify(p["name"], "-"), used)
            used.add(anchor)
            anchors[p["name"]] = f"categories/{key}.md#{anchor}"
            sections.append(project_section(p, now))
        intro = f"{len(items)} projects, most used and most active first."
        if tested:
            verb = "builds" if tested == 1 else "build"
            intro += f" {tested} of them {verb} on MkDocs NG in the weekly [compatibility test]({COMPAT_URL})."
        page = [
            "---",
            f"description: {len(items)} MkDocs {title.lower()} projects, ranked by usage and activity.",
            "---",
            "",
            f"# {title}",
            "",
            intro,
            "",
            f"Data from {data_date}.",
            "",
            *sections,
        ]
        write(f"categories/{key}.md", "\n".join(page))

    tested = [p for p in projects if p["tested"]]
    rows = [
        f"| [{p['name']}]({anchors[p['name']]}) | {categories[p['category']]} | {short_number(p['downloads'])} |"
        for p in sorted(tested, key=lambda p: -p["downloads"])
    ]
    write(
        "ng-tested.md",
        "\n".join(
            [
                "---",
                "description: Plugins and themes that build on MkDocs NG in the weekly compatibility test.",
                "---",
                "",
                "# Tested on MkDocs NG",
                "",
                f"Every week, MkDocs NG builds a small site with each of these {len(tested)} projects "
                f"in its [plugin compatibility test]({COMPAT_URL}). The list of cases lives in "
                f"[compat/cases.yaml]({CASES_URL}).",
                "",
                "| Project | Category | Downloads/month |",
                "| --- | --- | --- |",
                *rows,
                "{.striped}",
                "",
                "> [!NOTE]",
                "> A project that is not on this list is not broken: MkDocs NG keeps the MkDocs 1.x plugin API, "
                "so most plugins work unchanged. It just isn't in the weekly test yet.",
                "",
                "## Add a project to the test",
                "",
                f"Open a pull request that adds a case to [compat/cases.yaml]({CASES_URL}), "
                "or [open an issue](https://github.com/mkdocs-ng/mkdocs/issues/new) and name the plugin.",
                "",
            ]
        ),
    )

    cards = []
    for key, title in categories.items():
        items = by_category[key]
        if items:
            cards.append(f"- :lucide-{ICONS.get(key, 'package')}: [{title}](categories/{key}.md)\n  {len(items)} projects")
    top = sorted(projects, key=lambda p: -p["downloads"])[:15]
    top_rows = [
        f"| [{p['name']}]({anchors[p['name']]}) | {categories[p['category']]} | "
        f"{short_number(p['downloads'])} | {'✓' if p['tested'] else ''} |"
        for p in top
    ]
    total = len(projects)
    front_matter = {
        "template": "landing.html",
        "title": "MkDocs Catalog",
        "description": f"{total} plugins, themes and Markdown extensions for MkDocs NG and MkDocs, "
        "ranked by usage and activity and updated every week.",
        "hero": {
            "eyebrow": f"Updated every week · data from {data_date}",
            "title": "Find the right<br>*MkDocs plugin*.",
            "text": f"{total} plugins, themes and Markdown extensions for MkDocs NG and MkDocs, "
            "ranked by usage and activity. Marked when they build on MkDocs NG.",
            "actions": [
                {"text": "Browse categories", "link": "#categories", "icon": "layers", "primary": True},
                {"text": "Tested on MkDocs NG", "link": "ng-tested.md", "icon": "badge-check"},
                {"text": "GitHub", "link": "https://github.com/mkdocs-ng/catalog", "icon": "brand-github"},
            ],
            "install": "pip install mkdocs-ng",
        },
        "sections": [
            {
                "type": "stats",
                "items": [
                    {"value": str(total), "label": "projects"},
                    {"value": str(len(cards)), "label": "categories"},
                    {"value": str(len(tested)), "label": "tested on MkDocs NG every week"},
                ],
            },
            {"type": "content"},
        ],
    }
    body = [
        "## Categories",
        "",
        *cards,
        "{.cards}",
        "",
        "## Most downloaded",
        "",
        "| Project | Category | Downloads/month | Tested on NG |",
        "| --- | --- | --- | --- |",
        *top_rows,
        "{.striped}",
        "",
        "## How projects are ranked",
        "",
        "Projects are sorted by a quality score computed from GitHub and PyPI data: stars, downloads, "
        "dependents, contributors and recent activity. The data is refreshed every Thursday.",
        "",
        "## Add or update a project",
        "",
        "Edit [projects.yaml](https://github.com/mkdocs-ng/catalog/edit/main/projects.yaml) and open a pull request, "
        "or [suggest a project](https://github.com/mkdocs-ng/catalog/issues/new/choose) in an issue.",
        "",
        "## Start with MkDocs NG",
        "",
        "MkDocs NG is a maintained, drop-in replacement for MkDocs 1.x. Your `mkdocs.yml` and plugins keep working:",
        "",
        "```sh",
        "pip install mkdocs-ng",
        "```",
        "",
        "Read the [migration guide](https://mkdocs-ng.org/user-guide/migrating/) "
        "or try [Sumi](https://sumi.mkdocs-ng.org/), the MkDocs NG theme.",
        "",
    ]
    write(
        "index.md",
        "---\n" + yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=True) + "---\n\n" + "\n".join(body),
    )
    print(f"Wrote {len(cards) + 2} pages for {total} projects ({len(tested)} tested on NG) from {data_date}.")


if __name__ == "__main__":
    main()

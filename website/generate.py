"""Generate the catalog website's Markdown pages.

Reads the newest ``history/*_projects.csv`` (written by best-of every week) and
``projects.yaml`` (for categories and labels), and writes into ``website/docs/``,
next to the files from ``website/static/``:

- ``plugins/<name>.md``: one page per project, with install and config snippets;
- ``categories/<key>.md``: one summary table per category;
- ``all.md``: every project in one table, with filters (``static/js/catalog.js``);
- ``ng-tested.md`` and the home page.

Run it before ``mkdocs build``:

    python website/generate.py && mkdocs build -f website/mkdocs.yml
"""

from __future__ import annotations

import ast
import csv
import html
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import yaml
from markdown.extensions.toc import slugify, unique

ROOT = Path(__file__).resolve().parents[1]
DOCS = Path(__file__).resolve().parent / "docs"
STATIC = Path(__file__).resolve().parent / "static"
REPO = "https://github.com/mkdocs-ng/catalog"
COMPAT_URL = "https://github.com/mkdocs-ng/mkdocs/actions/workflows/compat.yml"
CASES_URL = "https://github.com/mkdocs-ng/mkdocs/blob/main/compat/cases.yaml"
NG_ISSUE_URL = "https://github.com/mkdocs-ng/mkdocs/issues/new?template=bug_report.yml&title="
INACTIVE_DAYS = 365
MAX_EXTENSIONS = 8
MAX_ALTERNATIVES = 5
SHORT_DESCRIPTION = 110

ICONS = {
    "theming": "palette",
    "api-docs": "code",
    "blogging": "pen-line",
    "charts": "chart-column",
    "code-exec-templating": "terminal",
    "git-info": "git-branch",
    "html-css": "paintbrush",
    "ai-llm": "sparkles",
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

# A project's main type, from its labels. A theme that also ships plugins is a theme.
TYPES = {"theme": "Theme", "plugin": "Plugin", "markdown": "Markdown extension", "project": "Other"}


def as_list(value) -> list[str]:
    """CSV cells hold a plain name or a Python list literal; YAML holds a string or a list."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
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


def cell(value: str) -> str:
    """Text that sits in a Markdown table cell."""
    return value.replace("|", "\\|")


def shorten(value: str, limit: int = SHORT_DESCRIPTION) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def main_type(labels: list[str]) -> str:
    for label in TYPES:
        if label in labels:
            return label
    return "project"


def yaml_lines() -> dict[str, int]:
    """The line of each ``- name:`` in projects.yaml, for "edit this entry" links."""
    lines = {}
    for number, line in enumerate((ROOT / "projects.yaml").read_text(encoding="utf-8").splitlines(), 1):
        match = re.match(r"- name:\s*(.+?)\s*$", line)
        if match:
            lines.setdefault(yaml.safe_load(match.group(1)), number)
    return lines


def load():
    config = yaml.safe_load((ROOT / "projects.yaml").read_text(encoding="utf-8"))
    labels = {p["name"]: as_list(p.get("labels")) for p in config["projects"]}
    categories = {c["category"]: plain_title(c["title"]) for c in config["categories"]}
    lines = yaml_lines()

    csv_path = sorted((ROOT / "history").glob("*_projects.csv"))[-1]
    data_date = csv_path.name.split("_")[0]
    with csv_path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("show", "True") == "True"]

    projects = []
    for row in rows:
        project_labels = labels.get(row["name"]) or as_list(row.get("labels", ""))
        projects.append(
            {
                "name": row["name"],
                "description": text(row.get("description", "")),
                "category": row["category"] if row["category"] in categories else "other",
                "type": main_type(project_labels),
                "pypi": row.get("pypi_id", ""),
                "github": row.get("github_url", ""),
                "homepage": row.get("homepage", ""),
                "license": row.get("license", ""),
                "theme": row.get("mkdocs_theme", ""),
                "plugins": as_list(row.get("mkdocs_plugin", "")),
                "extensions": as_list(row.get("markdown_extension", "")),
                "stars": as_number(row.get("star_count", "")),
                "downloads": as_number(row.get("monthly_downloads", "")),
                "dependents": as_number(row.get("dependent_project_count", "")),
                "contributors": as_number(row.get("contributor_count", "")),
                "rank": as_number(row.get("projectrank", "")),
                "release": row.get("latest_stable_release_number", ""),
                "release_date": as_date(row.get("latest_stable_release_published_at", "")),
                "last_commit": as_date(row.get("last_commit_pushed_at", "")),
                "new": row.get("new_addition", "") == "True",
                "tested": "ng-tested" in project_labels,
                "line": lines.get(row["name"]),
            }
        )
    if any(p["category"] == "other" for p in projects):
        categories["other"] = "Others"
    projects.sort(key=lambda p: (-p["rank"], -p["stars"], p["name"].lower()))

    used: set[str] = set()
    for p in projects:
        p["slug"] = unique(slugify(p["pypi"] or p["name"], "-") or "project", used)
        used.add(p["slug"])
    return categories, projects, data_date


def is_active(p: dict, now: datetime) -> bool:
    return bool(p["last_commit"]) and (now - p["last_commit"]).days <= INACTIVE_DAYS


def config_snippet(p: dict) -> list[str]:
    """The ``mkdocs.yml`` lines that turn the project on."""
    if p["theme"]:
        lines = ["theme:", f"  name: {p['theme']}"]
        if p["plugins"]:
            lines += ["", "# The theme also ships plugins. Add the ones you need:", "plugins:", "  - search"]
            lines += [f"  # - {name}" for name in p["plugins"]]
        return lines
    lines = []
    if p["plugins"]:
        lines += ["plugins:", "  - search  # keep the built-in search"]
        if len(p["plugins"]) == 1:
            lines.append(f"  - {p['plugins'][0]}")
        else:
            lines.append("  # This package ships several plugins. Add the ones you need:")
            lines += [f"  - {name}" for name in p["plugins"]]
    if p["extensions"]:
        if lines:
            lines.append("")
        lines.append("markdown_extensions:")
        lines += [f"  - {name}" for name in p["extensions"][:MAX_EXTENSIONS]]
        more = len(p["extensions"]) - MAX_EXTENSIONS
        if more > 0:
            lines.append(f"  # and {more} more, see the project's documentation")
    return lines


def project_page(p: dict, categories: dict, alternatives: list[dict], now: datetime, data_date: str) -> str:
    category = categories[p["category"]]
    badges = [f"**{TYPES[p['type']]}**{{.badge .accent}}"]
    if p["tested"]:
        badges.append("**Tested on MkDocs NG**{.badge .tip}")
    if p["last_commit"] and not is_active(p, now):
        badges.append("**Inactive**{.badge .warning}")
    if p["new"]:
        badges.append("**New**{.badge .new}")
    badges.append(f"in [{category}](../categories/{p['category']}.md)")

    description = p["description"] or f"An MkDocs {TYPES[p['type']].lower()}."
    page = [
        "---",
        "description: " + yaml.safe_dump(html.unescape(shorten(description, 160)), default_style='"', width=1000).strip(),
        "---",
        "",
        f"# {text(p['name'])}",
        "",
        description,
        "",
        " ".join(badges),
        "",
    ]

    if p["last_commit"] and not is_active(p, now):
        page += [
            "> [!WARNING]",
            f"> No commits since {p['last_commit']:%Y-%m-%d}. Check that it still fits your setup.",
            "",
        ]

    snippet = config_snippet(p)
    if p["pypi"] or snippet:
        page += ["## Install", ""]
        if p["pypi"]:
            page += ["```sh", f"pip install {p['pypi']}", "```", ""]
        if snippet:
            page += ["Then turn it on in `mkdocs.yml`:", "", "```yaml", *snippet, "```", ""]

    facts = []
    if p["stars"]:
        facts.append(("GitHub stars", f"{p['stars']:,}"))
    if p["downloads"]:
        facts.append(("Downloads", f"{p['downloads']:,} a month"))
    if p["dependents"]:
        facts.append(("Used by", f"{p['dependents']:,} projects"))
    if p["contributors"]:
        facts.append(("Contributors", f"{p['contributors']:,}"))
    if p["release"]:
        when = f" ({p['release_date']:%Y-%m-%d})" if p["release_date"] else ""
        facts.append(("Latest release", f"{p['release']}{when}"))
    if p["last_commit"]:
        facts.append(("Last commit", f"{p['last_commit']:%Y-%m-%d}"))
    if p["license"]:
        facts.append(("License", p["license"]))
    links = []
    if p["github"]:
        links.append(f"[:brand-github: GitHub]({p['github']})")
    if p["pypi"]:
        links.append(f"[:brand-pypi: PyPI](https://pypi.org/project/{p['pypi']}/)")
    if p["homepage"] and p["homepage"].rstrip("/") != p["github"].rstrip("/"):
        links.append(f"[:lucide-globe: Homepage]({p['homepage']})")
    if links:
        facts.append(("Links", " · ".join(links)))
    if facts:
        page += ["## Project data", "", "| | |", "| --- | --- |"]
        page += [f"| {label} | {cell(value)} |" for label, value in facts]
        page += ["{.catalog-facts}", "", f"Data from {data_date}, refreshed every Thursday.", ""]

    page += ["## MkDocs NG", ""]
    issue = NG_ISSUE_URL + quote(f"Plugin compatibility: {p['name']}")
    if p["tested"]:
        page += [
            f":lucide-badge-check: Builds on MkDocs NG every week in the [plugin compatibility test]({COMPAT_URL}).",
            "",
        ]
    else:
        page += [
            "Not in the weekly compatibility test yet. MkDocs NG keeps the MkDocs 1.x plugin API, "
            "so most projects work unchanged.",
            "",
            f"[Add it to the test]({CASES_URL}) · [Report a problem]({issue})",
            "",
        ]

    if alternatives:
        page += [
            "## Alternatives",
            "",
            f"The most used and most active projects in {category}:",
            "",
            "| Project | Description | Downloads/month |",
            "| --- | --- | --- |",
        ]
        page += [
            f"| [{cell(a['name'])}]({a['slug']}.md) | {cell(shorten(a['description']))} | {short_number(a['downloads'])} |"
            for a in alternatives
        ]
        page += ["{.compact}", ""]

    edit = f"{REPO}/blob/main/projects.yaml" + (f"#L{p['line']}" if p["line"] else "")
    page += [
        "---",
        "",
        f"Something wrong or missing? [Edit this entry]({edit}) in `projects.yaml` "
        f"or [open an issue]({REPO}/issues/new/choose).",
        "",
    ]
    return "\n".join(page)


def summary_table(items: list[dict], link_prefix: str, now: datetime) -> list[str]:
    """A category's projects as one table. Each row keeps the anchor the old project sections had."""
    used: set[str] = set()
    rows = ["| Project | Description | Downloads/month | Stars | Last commit | NG |", "| --- | --- | --- | --- | --- | --- |"]
    for p in items:
        anchor = unique(slugify(p["name"], "-"), used)
        used.add(anchor)
        last = f"{p['last_commit']:%Y-%m-%d}" if p["last_commit"] else ""
        if p["last_commit"] and not is_active(p, now):
            last = f"*{last}*"
        rows.append(
            f'| <span id="{anchor}"></span>[{cell(p["name"])}]({link_prefix}{p["slug"]}.md) '
            f"| {cell(shorten(p['description']))} | {short_number(p['downloads']) if p['downloads'] else ''} "
            f"| {short_number(p['stars']) if p['stars'] else ''} | {last} | {'✓' if p['tested'] else ''} |"
        )
    return rows + ["{.striped .compact .wide .catalog-summary}"]


def all_projects_table(projects: list[dict], categories: dict, now: datetime) -> list[str]:
    """Every project in one HTML table. static/js/catalog.js adds the filters and sorting."""
    options = "".join(
        f'<option value="{key}">{html.escape(title)}</option>' for key, title in categories.items()
    )
    types = "".join(f'<option value="{key}">{label}</option>' for key, label in TYPES.items())
    controls = (
        '<div class="catalog-filters" hidden>'
        '<input type="search" name="q" placeholder="Filter by name or description" aria-label="Filter by name or description">'
        f'<select name="category" aria-label="Category"><option value="">All categories</option>{options}</select>'
        f'<select name="type" aria-label="Type"><option value="">All types</option>{types}</select>'
        '<label><input type="checkbox" name="ng"> Tested on MkDocs NG</label>'
        '<label><input type="checkbox" name="active"> Active in the last year</label>'
        '<span class="catalog-count" aria-live="polite"></span>'
        "</div>"
    )
    head = (
        "<thead><tr>"
        '<th data-sort="text">Project</th><th data-sort="text">Type</th><th data-sort="text">Category</th>'
        '<th data-sort="number">Downloads/month</th><th data-sort="number">Stars</th>'
        '<th data-sort="number">Last commit</th><th data-sort="number">NG</th>'
        "</tr></thead>"
    )
    rows = []
    for index, p in enumerate(projects):
        last = p["last_commit"]
        active = is_active(p, now)
        name = html.escape(p["name"])
        description = shorten(p["description"])
        rows.append(
            f'<tr data-rank="{index}" data-category="{p["category"]}" data-type="{p["type"]}" '
            f'data-ng="{int(p["tested"])}" data-active="{int(active)}">'
            f'<td data-value="{name.lower()}"><a href="../plugins/{p["slug"]}/">{name}</a>'
            + (f'<br><small>{description}</small>' if description else "")
            + "</td>"
            f"<td>{TYPES[p['type']]}</td>"
            f'<td data-value="{html.escape(categories[p["category"]])}">'
            f'<a href="../categories/{p["category"]}/">{html.escape(categories[p["category"]])}</a></td>'
            f'<td data-value="{p["downloads"]}">{short_number(p["downloads"]) if p["downloads"] else ""}</td>'
            f'<td data-value="{p["stars"]}">{short_number(p["stars"]) if p["stars"] else ""}</td>'
            f'<td data-value="{int(last.timestamp()) if last else 0}"'
            + ("" if active or not last else ' class="catalog-inactive"')
            + f'>{f"{last:%Y-%m-%d}" if last else ""}</td>'
            f'<td data-value="{int(p["tested"])}">{"✓" if p["tested"] else ""}</td>'
            "</tr>"
        )
    table = (
        '<div class="sumi-table sumi-table--striped sumi-table--compact sumi-table--wide catalog-all">'
        f"<table>{head}<tbody>{''.join(rows)}</tbody></table></div>"
    )
    return [controls, "", table]


def write(name: str, content: str) -> None:
    path = DOCS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    categories, projects, data_date = load()
    now = datetime.now(timezone.utc)
    if DOCS.exists():
        shutil.rmtree(DOCS)
    shutil.copytree(STATIC, DOCS)  # logo, favicon, CSS, JS

    by_category = {key: [p for p in projects if p["category"] == key] for key in categories}

    for p in projects:
        alternatives = [a for a in by_category[p["category"]] if a is not p][:MAX_ALTERNATIVES]
        write(f"plugins/{p['slug']}.md", project_page(p, categories, alternatives, now, data_date))

    for key, title in categories.items():
        items = by_category[key]
        if not items:
            continue
        tested = sum(p["tested"] for p in items)
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
            *summary_table(items, "../plugins/", now),
            "",
            f"Data from {data_date}. Dates in *italics* are more than a year old. "
            f"[Filter all projects](../all.md) by type, NG status or activity.",
            "",
        ]
        write(f"categories/{key}.md", "\n".join(page))

    write(
        "all.md",
        "\n".join(
            [
                "---",
                f"description: All {len(projects)} MkDocs plugins, themes and Markdown extensions in one table.",
                "layout: wide",
                "---",
                "",
                "# All projects",
                "",
                f"{len(projects)} plugins, themes and Markdown extensions, most used and most active first. "
                f"Click a column to sort it. Data from {data_date}.",
                "",
                *all_projects_table(projects, categories, now),
                "",
            ]
        ),
    )

    tested = [p for p in projects if p["tested"]]
    rows = [
        f"| [{cell(p['name'])}](plugins/{p['slug']}.md) | {categories[p['category']]} | {short_number(p['downloads'])} |"
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

    # Projects by type and state, like the overview table of a package repository.
    stats = ["| | Projects | Tested on NG | Active in the last year | New this week |", "| --- | --- | --- | --- | --- |"]
    for key, label in [*TYPES.items(), ("", "**All**")]:
        items = [p for p in projects if not key or p["type"] == key]
        if not items:
            continue
        plural = label if key in ("", "project") else label + "s"
        stats.append(
            f"| {plural} | {len(items)} | {sum(p['tested'] for p in items)} "
            f"| {sum(is_active(p, now) for p in items)} | {sum(p['new'] for p in items)} |"
        )
    stats.append("{.striped}")

    top = sorted(projects, key=lambda p: -p["downloads"])[:15]
    top_rows = [
        f"| [{cell(p['name'])}](plugins/{p['slug']}.md) | {categories[p['category']]} | "
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
                {"text": "All projects", "link": "all.md", "icon": "table"},
                {"text": "Tested on MkDocs NG", "link": "ng-tested.md", "icon": "badge-check"},
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
        "## The catalog at a glance",
        "",
        *stats,
        "",
        f"Active means a commit in the last {INACTIVE_DAYS} days. [Filter all projects](all.md) by type, "
        "category, NG status or activity.",
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
        f"Edit [projects.yaml]({REPO}/edit/main/projects.yaml) and open a pull request, "
        f"or [suggest a project]({REPO}/issues/new/choose) in an issue. "
        "Every project page has an *Edit this entry* link to its line in the file.",
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
    print(
        f"Wrote {len(projects)} project pages and {len(cards) + 3} other pages "
        f"({len(tested)} tested on NG) from {data_date}."
    )


if __name__ == "__main__":
    main()

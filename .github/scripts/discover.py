#!/usr/bin/env python3
"""Find MkDocs plugins and themes on PyPI that the catalog doesn't list yet.

Every package on PyPI whose name contains "mkdocs" is a candidate. A candidate
counts as a plugin or theme when the wheel of its latest release declares a
`mkdocs.plugins` or `mkdocs.themes` entry point. Only `entry_points.txt` is
read from the wheel, through HTTP range requests, so a full scan takes a few
minutes and needs no token.

A package is skipped when it is already in projects.yaml, is listed in
discovery/ignore.yaml, has no GitHub repository, points to a repository the
catalog already lists (forks and re-uploads), or had no release in the last
two years. The rest become projects.yaml entries with a best-guess category:
printed by default, written to a file with --output, or appended to
projects.yaml with --add (the weekly update does this).

Usage: discover.py [--add] [--max-added 50] [--output FILE] [--max-age-days 730]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import configparser
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "mkdocs-ng-catalog-discovery (+https://github.com/mkdocs-ng/catalog)"
GITHUB_REPO = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)", re.IGNORECASE)
NOT_REPO_OWNERS = {"sponsors", "orgs", "topics", "features", "marketplace"}
# project_urls keys that point to the source, best first.
SOURCE_KEYS = ("source", "source code", "repository", "code", "github", "homepage", "home", "documentation")

# First match wins. Keys are catalog categories, values are regexes matched
# against the package name and summary.
CATEGORY_RULES = [
    ("i18n-l10n", r"\bi18n\b|\bl10n\b|translat|multilingual|multi-language|locali[sz]"),
    ("ai-llm", r"\bllms?\b|llms\.txt|\bai\b|chatbot|\bgpt\b|openai|\bmcp\b|\brag\b|ask ai|gemini|claude"),
    ("blogging", r"\bblog|\brss\b|\bfeed\b|\bposts?\b"),
    ("api-docs", r"\bapi\b|openapi|swagger|docstring|autodoc|graphql|protobuf|\bschema\b|argparse|\bclick\b|typer|\bcli\b"),
    ("site-conversion", r"\bpdf\b|epub|latex|\bdocx?\b|confluence|print|man ?page"),
    ("git-info", r"\bgit\b|revision|committer|author|changelog"),
    ("charts", r"diagram|chart|graph|mermaid|plantuml|drawio|draw\.io|excalidraw|\bimages?\b|gallery|lightbox|\btables?\b|\bsvg\b|\berd?\b|dbml|kroki|svgbob|\bplot"),
    ("code-exec-templating", r"jinja|macro|template|variable|execut|jupyter|notebook|pyodide|pyscript|\brun\b|\bexec\b"),
    ("links-refs", r"\blinks?\b|redirect|\burls?\b|bibtex|citation|bibliograph|glossary|abbreviation|footnote|wiki|backlink|autolink"),
    ("search-toc", r"\bsearch|\btoc\b|table of contents|index"),
    ("nav-pages", r"\bnav|navigation|pages?\b|sidebar|section|breadcrumb|\btags?\b|sitemap|ordering"),
    ("snippets-include", r"include|snippet|embed|import|external|reuse|partial"),
    ("quality", r"spell|lint|check|validat|test|languagetool|proofread"),
    ("html-css", r"\bhtml\b|\bcss\b|minif|style|font|icon|\bemoji|dark mode|light/dark|toggle|analytics|tracking|og image|opengraph|social card|meta"),
    ("integrations", r"obsidian|notion|jira|slack|github|gitlab|sphinx|backstage|techdocs|readthedocs|hugo|docusaurus|zensical"),
    ("site-management", r"build|deploy|version|monorepo|multirepo|cache|serve|draft|exclude|\benv\b|config"),
    ("markdown", r"markdown|admonition|callout|tabs?\b|superfences|pymdown|extension|syntax|highlight|math|katex|mathjax"),
]


def get(url: str, byte_range: str | None = None, attempts: int = 3) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if byte_range:
        request.add_header("Range", byte_range)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == attempts - 1:
                raise
        except OSError:
            if attempt == attempts - 1:
                raise
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


class RemoteFile(io.RawIOBase):
    """A read-only file over HTTP range requests, enough for zipfile."""

    def __init__(self, url: str, size: int):
        self.url, self.size, self.pos = url, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = (offset, self.pos + offset, self.size + offset)[whence]
        return self.pos

    def readinto(self, buffer) -> int:
        if self.pos >= self.size or not len(buffer):
            return 0
        end = min(self.pos + len(buffer), self.size) - 1
        data = get(self.url, f"bytes={self.pos}-{end}")
        buffer[: len(data)] = data
        self.pos += len(data)
        return len(data)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_entry_points(url: str, size: int) -> dict[str, list[str]]:
    with zipfile.ZipFile(io.BufferedReader(RemoteFile(url, size), buffer_size=64 * 1024)) as wheel:
        names = [n for n in wheel.namelist() if n.endswith(".dist-info/entry_points.txt")]
        if not names:
            return {}
        parser = configparser.ConfigParser(delimiters=("=",), interpolation=None)
        parser.optionxform = str
        parser.read_string(wheel.read(names[0]).decode("utf-8", "replace"))
        return {section: list(parser[section]) for section in parser.sections()}


def github_repo(info: dict) -> str | None:
    urls = {k.lower(): v for k, v in (info.get("project_urls") or {}).items() if v}
    ordered = [urls[k] for k in SOURCE_KEYS if k in urls] + list(urls.values()) + [info.get("home_page") or ""]
    for url in ordered:
        match = GITHUB_REPO.search(url)
        if match and match.group(1).lower() not in NOT_REPO_OWNERS:
            repo = match.group(2).removesuffix(".git").rstrip(".")
            return f"{match.group(1)}/{repo}"
    return None


def guess_category(name: str, summary: str, is_theme: bool) -> str | None:
    if is_theme:
        return "theming"
    text = f"{name.replace('-', ' ')} {summary}".lower()
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, text):
            return category
    return None


def inspect(name: str) -> dict:
    """PyPI metadata and entry points of the latest release, or an error."""
    try:
        data = json.loads(get(f"https://pypi.org/pypi/{name}/json"))
    except Exception as e:  # noqa: BLE001 - reported, not fatal
        return {"name": name, "error": f"metadata: {e}"}
    info, files = data["info"], data.get("urls") or []
    result = {
        "name": info["name"],
        "summary": (info.get("summary") or "").strip(),
        "released": min((f["upload_time_iso_8601"] for f in files), default=None),
        "github_id": github_repo(info),
        "yanked": all(f.get("yanked") for f in files) if files else False,
    }
    wheels = [f for f in files if f["packagetype"] == "bdist_wheel"]
    if not wheels:
        result["error"] = "no wheel"
        return result
    try:
        result["entry_points"] = wheel_entry_points(wheels[0]["url"], wheels[0]["size"])
    except Exception as e:  # noqa: BLE001
        result["error"] = f"wheel: {e}"
    return result


def catalog_ids(config: dict) -> tuple[set[str], set[str]]:
    pypi, repos = set(), set()
    for project in config["projects"]:
        if project.get("pypi_id"):
            pypi.add(normalize(project["pypi_id"]))
        if project.get("github_id"):
            repos.add(project["github_id"].lower())
    return pypi, repos


def to_project(package: dict, category: str | None) -> dict:
    entry_points = package["entry_points"]
    project = {"name": package["name"]}
    plugins = entry_points.get("mkdocs.plugins", [])
    themes = entry_points.get("mkdocs.themes", [])
    if themes:
        project["mkdocs_theme"] = themes[0] if len(themes) == 1 else themes
    if plugins:
        project["mkdocs_plugin"] = plugins[0] if len(plugins) == 1 else plugins
    project["github_id"] = package["github_id"]
    project["pypi_id"] = package["name"].replace("_", "-")
    labels = (["theme"] if themes else []) + (["plugin"] if plugins else [])
    project["labels"] = labels
    if category:
        project["category"] = category
    return project


class EntryPoints:
    """Plugin and theme names already taken, with check_projects.py's rules.

    When two projects provide the same name, MkDocs loads only one of them, so
    the later project must say `shadowed: [mkdocs_plugin]` (or `mkdocs_theme`).
    """

    def __init__(self):
        self.taken = {"mkdocs_plugin": set(), "mkdocs_theme": set()}

    def claim(self, project: dict) -> list[str]:
        shadowed = []
        for kind, taken in self.taken.items():
            items = project.get(kind) or []
            items = [items] if isinstance(items, str) else items
            clash = False
            for item in items:
                if item in taken or (kind == "mkdocs_plugin" and item.split("/")[-1] in taken):
                    clash = True
                else:
                    taken.add(item)
            if clash:
                shadowed.append(kind)
        return shadowed


def project_yaml(project: dict) -> str:
    """One projects.yaml entry, with lists in flow style like the rest of the file."""
    lines = []
    for key, value in project.items():
        if isinstance(value, list):
            value = "[" + ", ".join(yaml.safe_dump(v, default_style=None).strip().removesuffix("...").strip() for v in value) + "]"
        else:
            value = yaml.safe_dump(value, allow_unicode=True, width=1000).strip().removesuffix("...").strip()
        lines.append(("- " if not lines else "  ") + f"{key}: {value}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--max-age-days", type=int, default=730, help="skip packages without a newer release")
    parser.add_argument("--output", type=Path, help="write the candidates to this file")
    parser.add_argument("--add", action="store_true", help="append the candidates to projects.yaml")
    parser.add_argument("--max-added", type=int, default=50, help="with --add, refuse to add more than this")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    config = yaml.safe_load((ROOT / "projects.yaml").read_text())
    known_pypi, known_repos = catalog_ids(config)
    categories = {c["category"] for c in config["categories"]}
    taken = EntryPoints()
    for project in config["projects"]:
        taken.claim(project)
    ignore_file = ROOT / "discovery" / "ignore.yaml"
    ignored = {normalize(n) for n in (yaml.safe_load(ignore_file.read_text()) or [])} if ignore_file.exists() else set()

    index = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                "https://pypi.org/simple/",
                headers={"Accept": "application/vnd.pypi.simple.v1+json", "User-Agent": USER_AGENT},
            ),
            timeout=120,
        ).read()
    )
    names = sorted(p["name"] for p in index["projects"] if "mkdocs" in p["name"].lower())
    todo = [n for n in names if normalize(n) not in known_pypi and normalize(n) not in ignored]
    print(f"{len(names)} PyPI packages named *mkdocs*, {len(todo)} not in the catalog or ignore list", file=sys.stderr)

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        packages = list(pool.map(inspect, todo))

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.max_age_days)).isoformat()
    skipped: dict[str, list[str]] = {}
    candidates, seen_repos = [], set()
    for package in sorted(packages, key=lambda p: p.get("released") or "", reverse=True):
        entry_points = package.get("entry_points") or {}
        is_theme = "mkdocs.themes" in entry_points
        if package.get("error"):
            reason = package["error"].split(":")[0]
        elif not is_theme and "mkdocs.plugins" not in entry_points:
            reason = "no mkdocs entry point"
        elif package["yanked"]:
            reason = "yanked"
        elif not package["github_id"]:
            reason = "no GitHub repository"
        elif package["github_id"].lower() in known_repos:
            reason = "repository already listed"
        elif package["github_id"].lower() in seen_repos:
            reason = "same repository as another candidate"
        elif (package["released"] or "") < cutoff:
            reason = f"no release in {args.max_age_days} days"
        else:
            seen_repos.add(package["github_id"].lower())
            category = guess_category(package["name"], package["summary"], is_theme)
            category = category if category in categories else None
            project = to_project(package, category)
            shadowed = taken.claim(project)
            if shadowed:
                project = {**project, "shadowed": shadowed}
                project["labels"] = project.pop("labels")  # keep labels and category last
                if "category" in project:
                    project["category"] = project.pop("category")
            candidates.append((package, project))
            continue
        skipped.setdefault(reason, []).append(package["name"])

    entries = []
    for package, project in candidates:
        note = f"# {package['released'][:10]}: {package['summary']}".rstrip()
        entries.append(f"{note}\n{project_yaml(project)}")
    text = "\n".join(entries)

    for reason, items in sorted(skipped.items(), key=lambda kv: -len(kv[1])):
        print(f"  skipped {len(items):4} {reason}", file=sys.stderr)
    print(f"{len(candidates)} new plugins and themes", file=sys.stderr)
    for package, project in candidates:
        print(f"  + {package['name']} ({project.get('category', 'others')})", file=sys.stderr)

    if args.output:
        args.output.write_text(text)
    if args.add and candidates:
        if len(candidates) > args.max_added:
            print(f"Refusing to add {len(candidates)} projects at once (--max-added {args.max_added})", file=sys.stderr)
            return 1
        projects_file = ROOT / "projects.yaml"
        current = projects_file.read_text().rstrip("\n")
        # Comments become part of projects.yaml, so keep only the entries.
        added = "\n".join(project_yaml(project).rstrip("\n") for _, project in candidates)
        projects_file.write_text(f"{current}\n{added}\n")
        yaml.safe_load(projects_file.read_text())  # still valid YAML
    if not args.output and not args.add:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

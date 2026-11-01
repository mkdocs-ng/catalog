#!/usr/bin/env python3
"""Refuse a generated update that lost most of its data.

The generator keeps going when GitHub or PyPI requests fail, and a project
without GitHub metadata is silently dropped from the README. That once turned
a 300-project list into an 18-project one. Run this after `best-of generate`
and before committing.

Usage: check_update.py OLD_README NEW_README NEW_PROJECTS_CSV
"""

import csv
import re
import sys
from pathlib import Path

# The new list may shrink a little (projects removed from projects.yaml,
# repositories deleted upstream), but not by more than this.
MAX_PROJECT_LOSS = 0.10
# Share of projects with a GitHub repo that must come back with GitHub data.
MIN_GITHUB_COVERAGE = 0.90


def project_count(readme: str) -> int:
    match = re.search(r"This list contains (\d+) awesome", readme)
    if not match:
        sys.exit("Could not find the project count in the README.")
    return int(match.group(1))


def main(old_readme: str, new_readme: str, projects_csv: str) -> None:
    old = project_count(Path(old_readme).read_text())
    new = project_count(Path(new_readme).read_text())
    print(f"Projects in README: {old} -> {new}")

    with open(projects_csv, newline="") as f:
        rows = [row for row in csv.DictReader(f) if row.get("github_id")]
    with_data = sum(1 for row in rows if row.get("star_count"))
    coverage = with_data / len(rows) if rows else 1.0
    print(f"GitHub data: {with_data}/{len(rows)} projects ({coverage:.0%})")

    problems = []
    if new < old * (1 - MAX_PROJECT_LOSS):
        problems.append(f"the README lost {old - new} of {old} projects")
    if coverage < MIN_GITHUB_COVERAGE:
        problems.append(f"only {coverage:.0%} of GitHub projects got metadata")
    if problems:
        sys.exit("Refusing this update: " + "; ".join(problems) + ".")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])

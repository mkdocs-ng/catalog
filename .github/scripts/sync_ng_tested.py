#!/usr/bin/env python3
"""Set the `ng-tested` label from the MkDocs NG plugin compatibility report.

mkdocs-ng/mkdocs builds popular plugins on MkDocs NG every week
(compat/cases.yaml) and uploads the results as compat-report.json. A catalog
project gets the label when the case that installs its PyPI package passes on
MkDocs NG, and loses it otherwise. Only the label lines of projects.yaml change,
so the file keeps its formatting.

Usage: sync_ng_tested.py COMPAT_REPORT_JSON COMPAT_CASES_YAML [PROJECTS_YAML]
"""

import json
import re
import sys
from pathlib import Path

import yaml

LABEL = "ng-tested"


def package_name(requirement: str) -> str:
    """`mkdocstrings[python]>=1` -> `mkdocstrings`."""
    return re.split(r"[\[<>=!~; ]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def passing_packages(report: dict, cases: list) -> set:
    packages = set()
    for case in cases:
        result = report.get(case["name"], {}).get("ng", {})
        if result.get("status") == "pass":
            packages.add(package_name(case["install"][0]))
    return packages


def set_labels(lines: list, passing: set) -> list:
    """Add or remove the label in each project block; return the changes."""
    changes = []
    start = lines.index("projects:")
    blocks = []
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("- name:"):
            blocks.append([i])
        elif blocks:
            blocks[-1].append(i)

    # Walk from the end so inserted lines don't shift the blocks still to do.
    for block in reversed(blocks):
        name = lines[block[0]][len("- name:") :].strip()
        pypi = next((lines[i].split(":", 1)[1].strip() for i in block if lines[i].startswith("  pypi_id:")), "")
        tested = bool(pypi) and package_name(pypi) in passing
        label_line = next((i for i in block if lines[i].startswith("  labels:")), None)

        if label_line is None:
            if tested:
                category = next((i for i in block if lines[i].startswith("  category:")), block[-1] + 1)
                lines.insert(category, f"  labels: [{LABEL}]")
                changes.append(f"+ {name}")
            continue

        line = lines[label_line]
        labels = [x.strip() for x in line[line.index("[") + 1 : line.rindex("]")].split(",") if x.strip()]
        has = LABEL in labels
        if tested and not has:
            labels.append(LABEL)
            changes.append(f"+ {name}")
        elif has and not tested:
            labels.remove(LABEL)
            changes.append(f"- {name}")
        else:
            continue
        lines[label_line] = "  labels: [" + ", ".join(labels) + "]"
    return changes


def main(report_path: str, cases_path: str, projects_path: str = "projects.yaml") -> None:
    report = json.loads(Path(report_path).read_text())
    cases = yaml.safe_load(Path(cases_path).read_text())
    passing = passing_packages(report, cases)
    # A report where nothing passes means the run broke, not every plugin.
    if not passing:
        sys.exit("No passing cases in the compatibility report; leaving the labels alone.")

    path = Path(projects_path)
    lines = path.read_text().split("\n")
    changes = set_labels(lines, passing)
    path.write_text("\n".join(lines))
    yaml.safe_load(path.read_text())  # still valid YAML

    print(f"{len(passing)} packages pass on MkDocs NG.")
    print("\n".join(changes) if changes else "No label changes.")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])

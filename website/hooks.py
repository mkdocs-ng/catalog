"""Keep the project pages out of the sidebar.

generate.py writes one page per project under plugins/. They are reached from the
category tables, the All projects table and search, so the sidebar lists only the
handful of overview pages. Without this, every page would carry a nav of ~500 links.
"""

from __future__ import annotations

from mkdocs.structure.nav import Navigation, Section
from mkdocs.structure.pages import Page

HIDDEN = "plugins/"


def _visible(items: list) -> list:
    out = []
    for item in items:
        if isinstance(item, Section):
            item.children = _visible(item.children)
            if item.children:
                out.append(item)
        elif isinstance(item, Page) and item.file.src_uri.startswith(HIDDEN):
            item.parent = None
        else:
            out.append(item)
    return out


def _pages(items: list) -> list[Page]:
    pages = []
    for item in items:
        if isinstance(item, Section):
            pages += _pages(item.children)
        elif isinstance(item, Page):
            pages.append(item)
    return pages


def on_nav(nav: Navigation, **kwargs) -> Navigation:
    hidden = [p for p in nav.pages if p.file.src_uri.startswith(HIDDEN)]
    nav.items = _visible(nav.items)
    pages = _pages(nav.items)
    nav.pages = pages  # llms.txt then lists the project pages as pages outside the nav
    for i, page in enumerate(pages):
        page.previous_page = pages[i - 1] if i else None
        page.next_page = pages[i + 1] if i + 1 < len(pages) else None
    for page in hidden:
        page.previous_page = page.next_page = None
    return nav

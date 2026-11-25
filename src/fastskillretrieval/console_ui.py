"""Console feedback for ``init-index``: a banner and live progress, written to stderr.

stdout stays reserved for the JSON summary. Everything here is plain ASCII so it renders on any
Windows console code page.
"""

from __future__ import annotations

import sys

BANNER = r"""
                         _.-'~~`~~'-._
                       .'   _     _   '.        F A S T S K I L L
                      /    (o)   (o)    \       R E T R I E V A L
                      |     .-----.     |
                      \    / ~~~~~ \    /       "Abandon all guesswork,
                       '. /_______\ .'           ye who search here."
                    ____|  |||||||  |____
                   /    \__|||||||__/    \
                  /  ____/   |||   \____  \      Charon ferries your skills
                 |  /  |     |||     |  \  |     across to the index.
                 ' /   |    /|||\    |   \ '
                  /    |   / ||| \   |    \
          ________\____|__/__|||__\__|____/________
       ~~~\  CHARON                                /~~~
      ~~~~~\______________________________________/~~~~~
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"""

_ORIGIN_WIDTH = 28
_NAME_WIDTH = 44


def _out(text: str = "") -> None:
    print(text, file=sys.stderr, flush=True)


def banner(agent: str) -> None:
    _out(BANNER)
    _out(f"  Indexing the skills, agents and commands installed in: {agent}")
    _out("  Read-only on your files: originals are copied, never modified.")
    _out()


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _bar(position: int, total: int, width: int = 24) -> str:
    filled = width * position // total if total else width
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _show(position: int, total: int) -> bool:
    """Every item on a real terminal; one in 50 (and the last) when output is captured, e.g. by an agent."""
    return sys.stderr.isatty() or position % 50 == 0 or position == total


def on_stage(position: int, total: int, source) -> None:
    if not _show(position, total):
        return
    _out(
        f"  [1/2 reading ] {_bar(position, total)} {position:>4}/{total:<4} "
        f"{source.kind:<7} {_clip(source.display, _NAME_WIDTH):<{_NAME_WIDTH}} "
        f"({_clip(source.origin, _ORIGIN_WIDTH)})"
    )


def on_item(position: int, total: int, item: dict, outcome: str) -> None:
    if outcome != "ok" or _show(position, total):
        mark = "ok  " if outcome == "ok" else "SKIP"
    else:
        return
    _out(
        f"  [2/2 indexing] {_bar(position, total)} {position:>4}/{total:<4} "
        f"{mark} {_clip(item['name'], _NAME_WIDTH)}"
    )


def done(summary: dict) -> None:
    if summary.get("status") == "current":
        _out("  Index already up to date: nothing to do.")
        return
    found = summary.get("found", {})
    _out()
    _out(
        f"  Done in {summary.get('seconds')}s: {summary.get('published')} published "
        f"({found.get('skill', 0)} skills, {found.get('agent', 0)} agents, "
        f"{found.get('command', 0)} commands), {summary.get('skipped_count', 0)} skipped."
    )
    _out(f"  Index: {summary.get('index')}")

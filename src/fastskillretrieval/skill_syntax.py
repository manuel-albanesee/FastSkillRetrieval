"""Detect native skill templates without mistaking quoted identifiers for commands.

This module never evaluates the source. Standalone identifiers in inline code and
ordinary code fences in explicitly labelled anti-pattern sections are documentation.
Inline paths/commands, ordinary execution examples and shell injection fences still
require native rendering. This is a bounded conservative scan, not a Markdown engine.
"""

import re

_INLINE_INJECTION = re.compile(r"(?<!\S)!`[^`\r\n]+`")
_FENCED_INJECTION = re.compile(r"(?m)^[ \t]*`{3,}!")
_HOST_VARIABLE = re.compile(r"\$ARGUMENTS(?:\[\d+\])?(?!\w)|\$\{CLAUDE_[A-Z0-9_]+\}")
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$")
_COUNTEREXAMPLE_HEADINGS = {
    "anti-patterns",
    "anti patterns",
    "antipatterns",
    "counterexamples",
    "bad examples",
}


def _literal_identifier(match: re.Match) -> str:
    return " " if _HOST_VARIABLE.fullmatch(match[2].strip()) else match[0]


def has_native_substitutions(body: str) -> bool:
    """Separate native directives from documented names and forbidden examples."""
    # Actual injection syntax remains native even when documented inside a fence.
    if _INLINE_INJECTION.search(body) or _FENCED_INJECTION.search(body):
        return True
    if not _HOST_VARIABLE.search(body):
        return False
    fence: tuple[str, int] | None = None
    counterexample_level: int | None = None
    for line in body.splitlines():
        marker = _FENCE.match(line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]:
                if not marker[2].strip():
                    fence = None
                    continue
            if counterexample_level is None and _HOST_VARIABLE.search(line):
                return True
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading[1])
            if counterexample_level is not None and level <= counterexample_level:
                counterexample_level = None
            if heading[2].strip().casefold() in _COUNTEREXAMPLE_HEADINGS:
                counterexample_level = level
        if marker:
            fence = (marker[1][0], len(marker[1]))
            # A variable in the info string is still not a literal identifier.
            if _HOST_VARIABLE.search(marker[2]):
                return True
            continue
        if counterexample_level is None:
            prose = _INLINE_CODE.sub(_literal_identifier, line)
            if _HOST_VARIABLE.search(prose):
                return True
    return False

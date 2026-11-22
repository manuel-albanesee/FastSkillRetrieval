"""Small, explicit contracts shared by the CLI and MCP adapter."""

import json
import re
import unicodedata
from dataclasses import dataclass


class Failure(Exception):
    def __init__(self, status: str, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Limits:
    candidates: int = 5
    cards: int = 3600
    load: int = 65536
    period: int = 262144
    output_bytes: int = 65536
    skill_bytes: int = 262144
    package_bytes: int = 33554432
    package_files: int = 256
    candidate_bytes: int = 16384
    inventory_skills: int = 5000
    batch: int = 100

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("Limits must be positive integers")


def serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cost(value: object) -> int:
    """Conservative UTF-8 byte estimate; NOT an observed client tokenizer count."""
    return len(serialized(value).encode("utf-8"))


def text(value: object, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise Failure("needs_revision", "expected_text")
    result = unicodedata.normalize("NFC", value).strip()
    if not result or len(result) > maximum or "\x00" in result:
        raise Failure("needs_revision", "invalid_text_length")
    return result


TECHNOLOGIES = {
    "c",
    "cpp",
    "csharp",
    "python",
    "javascript",
    "typescript",
    "sqlite",
    "postgresql",
    "react",
    "nextjs",
    "cloudflare",
    "markdown",
    "json",
    "git",
}
ALIASES = {"c++": "cpp", "c#": "csharp", "js": "javascript", "ts": "typescript"}


def technology(value: str) -> str:
    if not isinstance(value, str):
        raise Failure("policy_denied", "invalid_technology")
    normalized = value.casefold().strip()
    normalized = ALIASES.get(normalized, normalized)
    if normalized not in TECHNOLOGIES:
        raise Failure("needs_revision", "unresolved_technology")
    return normalized


def _tokens(value: str) -> list[str]:
    value = unicodedata.normalize("NFC", value).casefold()
    value = re.sub(r"\bc\+\+", " cpp ", value)
    value = re.sub(r"\bc#", " csharp ", value)
    return list(dict.fromkeys(re.findall(r"[^\W_]+", value, flags=re.UNICODE)))


def terms(value: str) -> list[str]:
    """Extract literals, never forward raw FTS syntax. Preserve C++ and C#."""
    return _tokens(value)[:64]


# Query side only: the FTS index keeps every token (see terms). Technical tokens that
# can be a whole query (c, go, r, ai, ci, cd, ui, ux, ...) must never be listed here.
STOP_WORDS = frozenset(
    """
    a ad agli al all alla alle allo anche che chi col come con da dai dal dall dalla dalle
    dallo dei del dell della delle dello di due e è ed fra gia già gli ha hanno ho i il in l la
    le lo ma mi ne nei nel nell nella nelle nello non o per perche perché piu più quale quali
    quest se si sia sono su sui sul sull sulla sulle sullo ti tra un una uno cosi così
    the of to and for with an is are was were be been on at by from in as or its this that
    these those into about my your our their
    """.split()
)


# Italian elisions (c'è, dov'è, d'accordo): without this the stub letter, e.g. "c", would
# be searched as the C language.
_ELISION = re.compile(
    r"\b(?:c|l|d|dov|cos|com|quell|quest|dell|all|dall|nell|sull|un|anch)['’](?=\w)"
)


def query_terms(value: str) -> list[str]:
    """Content tokens of a search query: terms() minus Italian/English stop-words.

    Stop-words are dropped before the 64-token cap so they cannot push content tokens out.
    """
    value = _ELISION.sub(" ", unicodedata.normalize("NFC", value).casefold())
    return [token for token in _tokens(value) if token not in STOP_WORDS][:64]


def validate_candidate(candidate: object, files: dict[str, bytes], limits: Limits) -> dict:
    if cost(candidate) > limits.candidate_bytes:
        raise Failure("needs_revision", "candidate_too_large")
    fields = {"description", "aliases", "capabilities", "use_when", "exclusions", "technologies"}
    if not isinstance(candidate, dict) or set(candidate) != fields:
        raise Failure("needs_revision", "candidate_fields")
    result = {"description": text(candidate["description"])}
    if len(result["description"]) < 40:
        raise Failure("needs_revision", "description_too_short")
    for key in ("aliases", "use_when", "exclusions", "technologies"):
        values = candidate[key]
        if not isinstance(values, list) or len(values) > 32:
            raise Failure("needs_revision", f"invalid_{key}")
        values = [text(v, 80 if key == "aliases" else 1000) for v in values]
        if len({v.casefold() for v in values}) != len(values):
            raise Failure("needs_revision", f"duplicate_{key}")
        result[key] = values
    if not result["use_when"]:
        raise Failure("needs_revision", "missing_use_condition")
    result["technologies"] = sorted({technology(v) for v in result["technologies"]})
    capabilities = candidate["capabilities"]
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 32:
        raise Failure("needs_revision", "missing_capabilities")
    result["capabilities"] = []
    for item in capabilities:
        if not isinstance(item, dict) or set(item) != {"action", "object", "scope", "evidence"}:
            raise Failure("needs_revision", "capability_fields")
        capability = {key: text(item[key]) for key in ("action", "object", "scope")}
        evidence = item["evidence"]
        if not isinstance(evidence, dict) or set(evidence) != {"file", "quote"}:
            raise Failure("needs_revision", "evidence_fields")
        file = text(evidence["file"], 256)
        quote = text(evidence["quote"], 4000)
        if file not in files or quote not in files[file].decode("utf-8", errors="replace"):
            raise Failure("needs_revision", "evidence_not_in_source")
        capability["evidence"] = {"file": file, "quote": quote}
        result["capabilities"].append(capability)
    return result

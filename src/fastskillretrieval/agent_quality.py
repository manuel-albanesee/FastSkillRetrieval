"""Deterministic quality check of agent descriptions, applied before indexing.

Retrieval is lexical (BM25), so an agent described in one language is invisible to requests written in the
other. ``analyze`` reports the defects, ``correct`` fixes what can be fixed without a model: it strips
corrupt characters and, when a description speaks one language about a glossary concept, appends the
other language's search terms.
"""

from __future__ import annotations

import re

QUALITY_VERSION = 3 # bump when analyze/correct change: it is part of the index signature
MAX_LENGTH = 1000
MIN_LENGTH = 60

_WORD = re.compile(r"[a-zà-ÿ0-9]+")
_TRIGGER = re.compile(
    r"\b(use (when|this|for|it|proactively|immediately|after|before)|proactively|must be used|"
    r"when|if you|quando|usare|usa|da usare|se serve|invoca\w*)\b",
    re.IGNORECASE,
)

_EN_WORDS = frozenset(
    "the and or for with when to of this that is are use your you in on a an be by as it from".split()
)
_IT_WORDS = frozenset(
    "il lo la le gli i di che per con una un non è sono del della dei delle quando usa uso nel nella "
    "da su come anche più se".split()
)

# (italian stems, english stems, italian terms to add, english terms to add); stems match word starts.
_GLOSSARY = (
    (("documentazion",), ("documentation", "docs", "codemap"),
     ("documentazione", "documentazioni"), ("documentation", "docs")),
    (("prestazion", "lent", "bottiglia"), ("performance", "slow", "bottleneck", "latency"),
     ("prestazioni", "lento", "colli", "bottiglia"), ("performance", "slow", "bottleneck")),
    (("pianific", "piano", "piani"), ("plan", "planning", "roadmap"),
     ("pianifica", "piano", "pianificazione"), ("plan", "planning")),
    (("ristruttur",), ("refactor",), ("ristrutturazione",), ("refactor", "refactoring")),
    (("sicurezza", "vulnerabil", "segret"), ("security", "vulnerab", "secret"),
     ("sicurezza", "vulnerabilità", "segreti"), ("security", "vulnerability", "secret")),
    (("libreria", "librerie"), ("library", "libraries"), ("libreria", "librerie"), ("library", "libraries")),
    (("errore", "errori"), ("error", "errors"), ("errore", "errori"), ("error", "errors")),
    (("compila", "compilazione"), ("build", "compile", "compilation"),
     ("compila", "compilazione"), ("build", "compile")),
    (("revisione", "controlla", "rivedi"), ("review",), ("revisione", "controlla", "rivedi"), ("review",)),
    (("ottimizz",), ("optimiz", "optimis"), ("ottimizza", "ottimizzare"), ("optimize",)),
    (("ricerca", "cerca", "trova"), ("search", "find", "lookup"),
     ("ricerca", "cerca", "trova"), ("search", "find")),
    (("flusso", "flussi", "traccia"), ("trace", "flow"), ("flusso", "traccia"), ("trace", "flow")),
    (("e2e",), ("end-to-end", "e2e"), ("end-to-end", "e2e"), ("end-to-end", "e2e")),
    (("mort", "inutilizz", "pulisc", "pulizi"), ("dead", "cleanup", "unused", "clean"),
     ("morto", "pulisci", "pulizia", "inutilizzate", "inutilizzato"), ("dead", "cleanup", "unused")),
    (("semplific",), ("simplif",), ("semplifica", "semplificare", "semplice"), ("simplify", "simplification")),
    (("progett", "architettur"), ("design", "architect"),
     ("progetta", "progettare", "architettura"), ("design", "architecture")),
    (("spieg", "esistent", "esplor"), ("explain", "explor", "existing", "understand"),
     ("spiega", "spiegare", "esistente", "esplora"), ("explain", "existing", "explore")),
    (("rete", "reti", "connettivit", "diagnos", "diagnostic"), ("network", "connectivity", "troubleshoot"),
     ("rete", "reti", "connettività", "diagnostica", "diagnosi"), ("network", "connectivity", "diagnose")),
    (("memoria", "caricamento", "carico"), ("memory", "load"),
     ("memoria", "caricamento", "carico"), ("memory", "load", "loading")),
    (("implementar", "implementaz"), ("implement",), ("implementa", "implementare", "implementazione"),
     ("implement", "implementation")),
    (("guidat", "tdd"), ("tdd", "test-driven", "driven"), ("tdd", "guidato", "prima", "implementazione"),
     ("tdd", "test-driven")),
    (("aggiorna", "aggiornare"), ("update", "updater"), ("aggiorna", "aggiornare"), ("update", "updates")),
)

# English concepts that users phrase with other words: (stems present, terms to add) regardless of language.
_EXTRA = (
    (("documentation", "docs"), ("docs", "readme", "document")),
    (("updater", "update"), ("update", "updated", "updates")),
    (("compil", "build"), ("fail", "fails", "failing", "failed")),
    (("plan",), ("plan", "planning")),
)

_SUFFIXES = tuple(sorted(("ing", "ed", "es", "s", "er", "ers", "ion", "ions", "ation", "ment", "ive", "ity"),
                         key=len, reverse=True))
_STEM_MIN = 4
_STEM_LIMIT = 20
_STOP = _EN_WORDS | _IT_WORDS | frozenset(
    "will would should could can may might must have has had does did not but also any all each more most "
    "other such than then there these those their they them what which while who how into onto over under "
    "about after before between during only same very just like using used uses make makes made get gets "
    "per tra fra sono sia sua suo nel nei sul sui alle agli".split()
)


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _has(words: list[str], stems: tuple[str, ...]) -> bool:
    return any(word.startswith(stem) for word in words for stem in stems)


def analyze(name: str, description: str) -> list[str]:
    """Stable defect codes for a description (empty list when clean)."""
    defects: list[str] = []
    if len(description.strip()) < MIN_LENGTH:
        defects.append("too_short")
    if not _TRIGGER.search(description):
        defects.append("no_trigger")
    if "�" in description:
        defects.append("corrupt_chars")
    words = _words(description)
    english = sum(word in _EN_WORDS for word in words)
    italian = sum(word in _IT_WORDS for word in words)
    if (english and not italian) or (italian and not english):
        defects.append("single_language")
    return defects


def _bilingual_terms(name: str, text: str) -> list[str]:
    words = _words(f"{name} {text}")
    present = {word for word in words}
    added: list[str] = []
    for it_stems, en_stems, it_terms, en_terms in _GLOSSARY:
        has_it, has_en = _has(words, it_stems), _has(words, en_stems)
        if has_it == has_en:
            continue
        for term in en_terms if has_it else it_terms:
            if term not in present and term not in added:
                added.append(term)
    return added


def clean(description: str) -> str:
    """Description without corrupt characters or double spaces."""
    return re.sub(r"\s+", " ", description.replace("�", " ")).strip()


def _stem(word: str) -> str | None:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _STEM_MIN:
            stem = word[: -len(suffix)]
            if len(stem) > _STEM_MIN and stem[-1] == stem[-2] and stem[-1] not in "aeiouls":
                stem = stem[:-1]  # planning -> plann -> plan
            return stem
    return None


def _stems(words: list[str], present: set[str]) -> list[str]:
    found: list[str] = []
    for word in words:
        if len(word) < _STEM_MIN + 1 or not word.isalpha() or word in _STOP:
            continue
        stem = _stem(word)
        if stem and stem not in present and stem not in _STOP and stem not in found:
            found.append(stem)
    return found[:_STEM_LIMIT]


def search_terms(name: str, description: str) -> list[str]:
    """Terms to index as aliases: the other language's glossary terms plus light English stems.

    Only terms absent from the name and description are returned, in a stable order."""
    text = clean(description)
    words = _words(f"{name} {text}")
    present = set(words)
    terms = _bilingual_terms(name, text)
    for stems, extra in _EXTRA:
        if _has(words, stems):
            terms += [term for term in extra if term not in present and term not in terms]
    terms += [stem for stem in _stems(_words(text), present | set(terms)) if stem not in terms]
    return terms


def alias_lines(terms: list[str], width: int = 80) -> list[str]:
    """Pack terms into space-separated strings of at most ``width`` characters."""
    lines: list[str] = []
    current = ""
    for term in terms:
        term = term[:width]
        if current and len(current) + 1 + len(term) > width:
            lines.append(current)
            current = term
        else:
            current = f"{current} {term}" if current else term
    if current:
        lines.append(current)
    return lines


def correct(name: str, description: str, limit: int = MAX_LENGTH, keywords: bool = True) -> str:
    """Description without corrupt characters or double spaces, plus bilingual search terms if useful.

    The result is at most ``limit`` characters; the terms line is kept and the original text is cut.
    With ``keywords=False`` only the cleaned text is returned (the terms go to the aliases instead)."""
    text = clean(description)
    terms = _bilingual_terms(name, text) if keywords else []
    if not terms:
        return text[:limit].rstrip()
    extra = " Keywords: " + " ".join(terms)
    extra = extra[:limit]
    return (text[: limit - len(extra)].rstrip() + extra)[:limit]

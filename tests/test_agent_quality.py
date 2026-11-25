import re

from fastskillretrieval import agent_index
from fastskillretrieval.agent_index import PREFIX, _candidate, signature
from fastskillretrieval.agent_quality import QUALITY_VERSION, alias_lines, analyze, clean, correct, search_terms

GOOD = "Use when a build fails: fixes TypeScript compile errors and the type checker output quickly"


def test_analyze_flags_each_defect():
    assert analyze("x", "short") == ["too_short", "no_trigger"]
    assert "corrupt_chars" in analyze("x", "GAN Harness � use when generating things for the team")
    assert "no_trigger" not in analyze("x", GOOD)
    assert "single_language" in analyze("x", GOOD)
    assert "single_language" not in analyze("x", "Use quando the build fails, per the team e the rest of it")


def test_correct_adds_other_language_terms_only_when_missing():
    english = correct("x", GOOD)
    assert english.startswith(GOOD) and "errore" in english and "compila" in english
    both = "Use when error or errore, build o compila, in the build"
    assert correct("x", both) == both
    assert correct("x", "Plain text without glossary concepts, used when needed for anything") == (
        "Plain text without glossary concepts, used when needed for anything"
    )
    italian = correct("x", "Pianifica il lavoro in fasi, usa quando serve un piano")
    assert "plan" in italian and "planning" in italian


def test_correct_without_keywords_only_cleans():
    assert correct("x", GOOD, keywords=False) == GOOD
    assert correct("x", "GAN �  Harness   tool", keywords=False) == "GAN Harness tool"
    long = GOOD + " " + "word " * 300
    assert correct("x", long, 100, keywords=False) == long[:100].rstrip()
    assert clean("a �  b") == "a b"


def test_search_terms_cover_glossary_and_stems():
    text = "Use when you plan the implementation and update the documentation"
    terms = search_terms("agent--x", text)
    assert "implement" in terms and "document" in terms and "pianifica" in terms
    assert not set(terms) & set(re.findall(r"[a-z0-9]+", text.lower()))
    assert "plan" in search_terms("x", "Use when planning the new billing feature for the team")
    assert "review" in search_terms("x", "Use when a reviewer checks the changes made by the team")
    assert "morto" in search_terms("x", "Use when removing dead code and unused dependencies")
    assert "simplify" in search_terms("x", "Semplifica il codice quando e troppo complicato per il team")
    many = " ".join(f"{chr(97 + i)}{chr(97 + i)}{chr(98 + i % 20)}wordings" for i in range(26))
    assert len(search_terms("x", many)) <= 20
    assert search_terms("x", GOOD) == search_terms("x", GOOD)


def test_alias_lines_are_bounded():
    lines = alias_lines(["parola"] * 40)
    assert len(lines) > 1 and all(len(line) <= 80 for line in lines)
    assert " ".join(lines).split() == ["parola"] * 40
    assert alias_lines(["x" * 200]) == ["x" * 80]
    assert alias_lines([]) == []


def test_correct_cleans_and_bounds():
    assert correct("x", "GAN �  Harness   tool") == "GAN Harness tool"
    long = correct("x", "review " + "word " * 400, 1000)
    assert len(long) <= 1000 and "Keywords:" in long and "revisione" in long
    assert correct("x", GOOD) == correct("x", GOOD)


def _stage(tmp_path, folder, description):
    target = tmp_path / folder
    target.mkdir()
    (target / "SKILL.md").write_text(
        f"---\nname: {folder}\ndescription: {description}\n---\nBody line.\n", encoding="utf-8"
    )


def test_candidate_corrects_agents_only(tmp_path):
    _stage(tmp_path, "agent--perf-tuner", "Profiles performance problems and slow endpoints in services")
    _stage(tmp_path, "perf-tuner", "Profiles performance problems and slow endpoints in services")
    agent = _candidate(tmp_path, {"name": "agent--perf-tuner"})
    skill = _candidate(tmp_path, {"name": "perf-tuner"})
    assert agent["description"].startswith("perf tuner: Profiles performance")
    assert "Keywords" not in agent["description"] and len(agent["description"]) <= 1000
    assert agent["description"] == skill["description"]
    assert agent["aliases"][0] == "agent--perf-tuner" and "prestazioni" in " ".join(agent["aliases"])
    assert all(len(alias) <= 80 for alias in agent["aliases"])
    assert skill["aliases"] == ["perf-tuner"]
    assert skill["description"] == "perf tuner: Profiles performance problems and slow endpoints in services"


def test_quality_version_is_three():
    assert QUALITY_VERSION == 3


def test_signature_depends_on_quality_version(monkeypatch):
    before = signature([])
    monkeypatch.setattr(agent_index, "QUALITY_VERSION", QUALITY_VERSION + 1)
    assert signature([]) != before
    assert PREFIX["agent"] == "agent--"

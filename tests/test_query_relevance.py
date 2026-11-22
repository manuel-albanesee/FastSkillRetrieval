"""Query-side stop-words, relevance threshold and card-budget accounting."""

import json
import shutil
from dataclasses import replace

import test_registry as shared

from fastskillretrieval.contracts import Limits, cost, query_terms, terms

registry = shared.registry
QUOTE = shared.metadata()["capabilities"][0]["evidence"]


def candidate(description, use_when, technologies, aliases=(), obj="data"):
    return {
        "description": description,
        "aliases": list(aliases),
        "capabilities": [
            {"action": "Audit", "object": obj, "scope": "local project", "evidence": QUOTE}
        ],
        "use_when": list(use_when),
        "exclusions": ["deployment"],
        "technologies": list(technologies),
    }


def publish_named(store, candidates, session="setup"):
    """Publish each discovered folder with the candidate chosen by its name."""
    inventory = store.call("discover", session)
    assert inventory["status"] == "ok", inventory
    for item in inventory["items"]:
        submitted = store.call(
            "submit",
            session,
            capability_id=item["id"],
            source_revision=item["source_revision"],
            candidate=candidates.get(item["name"], shared.metadata()),
        )
        assert submitted["status"] == "ok", submitted
    assert store.call("publish", session)["status"] == "ok"
    return {item["name"]: item["id"] for item in inventory["items"]}


def clone(source, *names, capability=None):
    for name in names:
        shutil.copytree(source / "markdown-check", source / name)
        if capability:
            (source / name / "capability.json").write_text(json.dumps(capability))


def selected(store, **arguments):
    result = store.call("search", "reader", **arguments)
    return result, {c["reference"]["capability_id"] for c in result["candidates"]}


def one_card_budget(store):
    """Smallest budget that fits exactly one markdown card, measured, not guessed."""
    probe = store.call("search", "probe", request_id="probe", query="markdown", limit=1)
    assert probe["status"] == "ok" and len(probe["candidates"]) == 1, probe
    return cost({**probe, "blocked": []}) + 8


def test_default_budgets_fit_full_manuals_with_five_search_cards():
    limits = Limits()
    assert limits.cards == 3600
    assert (limits.period, limits.load, limits.candidates) == (262144, 65536, 5)
    assert replace(limits, cards=1200).cards == 1200


def test_query_terms_drop_italian_and_english_stop_words():
    assert query_terms("Progetta una API REST per gli ordini e la sicurezza") == [
        "progetta",
        "api",
        "rest",
        "ordini",
        "sicurezza",
    ]
    assert query_terms("review the markdown and links of an api") == [
        "review",
        "markdown",
        "links",
        "api",
    ]


def test_query_terms_keep_technical_tokens_and_literals():
    assert query_terms("c C++ C# go r ai ci cd ui ux") == [
        "c",
        "cpp",
        "csharp",
        "go",
        "r",
        "ai",
        "ci",
        "cd",
        "ui",
        "ux",
    ]
    assert query_terms("e in per due") == []
    assert query_terms("") == []


def test_query_terms_cap_counts_content_tokens_not_stop_words():
    # Stop-words must not use up slots of the 64-token cap before the content token.
    query = " ".join(f"w{i}" for i in range(63)) + " e in per di markdown"
    assert query_terms(query)[-1] == "markdown"
    assert len(query_terms(query)) == 64


def test_query_terms_drop_italian_elisions_instead_of_leaving_letters():
    assert query_terms("c'è un bug") == ["bug"]
    assert query_terms("dov'è l'API") == ["api"]
    assert query_terms("cos’è Go") == ["go"]
    assert query_terms("d'accordo con l'altro") == ["accordo", "altro"]
    # A bare technical letter is still a term.
    assert query_terms("linguaggio c") == ["linguaggio", "c"]


def test_terms_is_unchanged_so_the_index_still_stores_stop_words():
    assert terms("Review the markdown e in per") == ["review", "the", "markdown", "e", "in", "per"]


def test_only_stop_words_is_no_match_even_when_indexed_text_contains_them(registry):
    store, _ = registry
    shared.publish(store)  # indexed text contains "and" and "e"
    result, found = selected(store, query="e and per the")
    assert result["status"] == "no_match", result
    assert found == set()


def test_stop_words_do_not_prevent_a_single_content_token_match(registry):
    store, _ = registry
    shared.publish(store)
    result, found = selected(store, query="il markdown e la")
    assert result["status"] == "ok", result
    assert len(found) == 1


def test_repeated_query_tokens_count_once(registry):
    store, source = registry
    clone(source, "sql-audit")
    sql = candidate("Audit SQLite query plans for slow lookups.", ["Audit SQL speed"], ["sqlite"])
    ids = publish_named(store, {"sql-audit": sql})
    result, found = selected(store, query="sqlite SQLite sqlite")
    assert found == {ids["sql-audit"]}, result


def test_cached_multi_token_search_replays_identically(registry):
    store, _ = registry
    shared.publish(store)
    query = "markdown documentation query-secret-unique-123"
    first = store.call("search", "user", request_id="same", query=query)
    assert first["status"] == "ok", first
    assert store.call("search", "user", request_id="same", query=query) == first
    changed = store.call("search", "user", request_id="same", query="markdown headings")
    assert changed["status"] == "request_conflict"


def test_omitted_for_budget_counts_only_filtered_in_candidates(registry):
    store, source = registry
    clone(source, "a-two", "a-three", "a-four")
    ids = publish_named(store, {})
    store.limits = replace(store.limits, cards=one_card_budget(store))
    result, found = selected(
        store, request_id="budget", query="markdown headings", exclude_ids=[ids["a-four"]]
    )
    assert result["status"] == "ok", result
    assert len(found) == 1
    # markdown-check, a-two and a-three are eligible: one fits, two are omitted.
    # a-four (excluded) must not be counted.
    assert result["omitted_for_budget"] == 2, result


def test_omitted_for_budget_ignores_technology_and_kind_filtered_out(registry):
    store, source = registry
    clone(source, "a-two", "off-tech")
    other = candidate(
        "Review Markdown headings inside other documents.", ["Review Markdown"], ["json"]
    )
    ids = publish_named(store, {"off-tech": other})
    store.limits = replace(store.limits, cards=one_card_budget(store))
    result, found = selected(
        store, request_id="tech", query="markdown headings", technologies=["markdown"]
    )
    assert result["status"] == "ok", result
    assert ids["off-tech"] not in found
    assert result["omitted_for_budget"] == 1, result  # only a-two or markdown-check
    result, _ = selected(store, request_id="kind", query="markdown headings", kinds=["workflow"])
    assert result["status"] == "no_match" and result["omitted_for_budget"] == 0, result


def test_alias_and_target_omitted_for_budget_count_once(registry):
    store, source = registry
    target = shared.publish(store)[0]["id"]
    alias = {"kind": "command", "activation": {"mode": "alias"}, "alias_of": target}
    clone(source, "command-alias", capability=alias)
    shared.publish(store, "alias-publication")
    base = store.call("search", "probe", request_id="empty", query="zzzz")
    assert base["status"] == "no_match", base
    store.limits = replace(store.limits, cards=cost(base) + 20)  # no card fits
    result = store.call("search", "reader", request_id="alias", query="markdown")
    assert result["status"] == "budget_exceeded", result
    assert result["candidates"] == []
    assert result["omitted_for_budget"] == 1, result


def test_blocked_detail_truncation_is_not_counted_as_omitted_for_budget(registry):
    store, source = registry
    blocked = {"kind": "skill", "requires": ["gone"]}
    clone(source, "b-one", "b-two", "b-three", capability=blocked)
    shared.publish(store)
    store.limits = replace(store.limits, cards=one_card_budget(store))
    result = store.call("search", "reader", request_id="blocked", query="markdown")
    assert result["status"] == "ok", result
    assert result["blocked_count"] == 3
    assert len(result["blocked"]) < 3  # details are cut to fit the same envelope
    assert result["omitted_for_budget"] == 0, result

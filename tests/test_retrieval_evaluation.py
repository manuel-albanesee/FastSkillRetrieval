from fastskillretrieval.retrieval_evaluation import classify_findings, summarize_cases


def test_metrics_keep_essential_losses_and_language_separate():
    rows = [
        dict(id="it", language="it", group="g", essential=["a", "b"], selected=["a"]),
        dict(id="en", language="en", group="g", essential=["a"], selected=["a"]),
    ]
    result = summarize_cases(rows)
    assert result["essential_recall"] == 2 / 3
    assert result["missing_essential"] == 1
    assert result["precision_at_5"] == 0.2
    assert result["complete_cases"] == 1
    assert result["semantic_groups"] == 1
    assert result["by_language"]["it"]["essential_recall"] == 0.5


def test_findings_do_not_invent_nodes(tmp_path):
    (tmp_path / "external").mkdir()
    (tmp_path / "external/SKILL.md").write_text("manual")
    graph = {
        "nodes": [{"id": "id", "name": "root"}],
        "findings": [
            {"source": "id", "reference": "external", "file": "SKILL.md", "line": 1},
            {"source": "id", "reference": "---", "file": "SKILL.md", "line": 2},
        ],
    }
    result = classify_findings(graph, tmp_path)
    assert [r["classification"] for r in result] == ["outside_frozen_corpus", "non_skill_syntax"]
    assert len(graph["nodes"]) == 1

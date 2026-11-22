# ruff: noqa: E501
"""Generate the documented v0.2/v0.3 comparison chart from recorded evidence."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "doc" / "Capability_Comparison_v0.2_v0.3.svg"


def bar(value: float, maximum: float, width: float) -> float:
    return 0 if maximum == 0 else width * value / maximum


def rect(x: float, y: float, width: float, height: float, colour: str) -> str:
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" fill="{colour}" rx="4"/>'


def text(
    x: float, y: float, value: str, size: int = 22, colour: str = "#172033", anchor: str = "start"
) -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{colour}" text-anchor="{anchor}">{escape(value)}</text>'


def paired_bars(
    parts: list[str],
    *,
    x: float,
    label: str,
    old: float,
    new: float,
    y: float,
    maximum: float,
    suffix: str = "",
) -> None:
    width = 520
    parts.append(text(x, y - 12, label, 21))
    parts.append(rect(x, y, bar(old, maximum, width), 20, "#64748b"))
    parts.append(rect(x, y + 29, bar(new, maximum, width), 20, "#0f766e"))
    parts.append(text(x + width + 18, y + 17, f"v0.2  {old:g}{suffix}", 19, "#475569"))
    parts.append(text(x + width + 18, y + 46, f"v0.3  {new:g}{suffix}", 19, "#0f766e"))


def main() -> None:
    old = json.loads((ROOT / "doc" / "Capability_Verifica.json").read_text(encoding="utf-8"))
    new = json.loads((ROOT / "doc" / "Capability_Verifica_v0.3.json").read_text(encoding="utf-8"))
    old_benchmark = next(item for item in old["benchmarks"] if item["size"] == 5000)
    new_benchmark = new["ingestion_benchmark"]

    old_tests = old["tests"]["passed"]
    new_tests = new["local_verification"]["pytest"]["passed"]
    old_mcp = old["host"]["legacy_mcp_calls"]
    new_mcp = new["host_observations"]["registry_mcp_calls"]
    old_runs = old["host"]["repetitions"]
    new_runs = new["host_observations"]["native_operator_runs"]
    old_coverage = old["tests"]["coverage"]["percent_covered"]
    new_coverage = new["local_verification"]["pytest"]["coverage_percent"]

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1110" viewBox="0 0 1600 1110">',
        "<style>text { font-family: Segoe UI, Arial, sans-serif; } .caption { font-size: 18px; fill: #475569; }</style>",
        '<rect width="1600" height="1110" fill="#f8fafc"/>',
        text(80, 72, "FastSkillRetrieval — confronto evidenze v0.2 → v0.3", 38, "#0f172a"),
        text(
            80,
            108,
            "Le barre confrontano soltanto misure registrate. I pannelli mantengono unità diverse separate.",
            20,
            "#475569",
        ),
        rect(70, 145, 710, 420, "#ffffff"),
        rect(820, 145, 710, 420, "#ffffff"),
        rect(70, 605, 1460, 410, "#ffffff"),
        text(100, 190, "Ampiezza delle verifiche", 28, "#0f172a"),
        text(850, 190, "Copertura e maturità del corpus", 28, "#0f172a"),
        text(100, 650, "Latenza di retrieval a 5.000 capability", 28, "#0f172a"),
        text(
            100,
            682,
            "Misure non equivalenti: v0.2 usa fixture SQL riusata; v0.3 include ingestion/pubblicazione reale e ripresa.",
            18,
            "#9f1239",
        ),
        '<rect x="100" y="215" width="18" height="18" fill="#64748b" rx="3"/><text x="128" y="231" class="caption">v0.2</text>',
        '<rect x="210" y="215" width="18" height="18" fill="#0f766e" rx="3"/><text x="238" y="231" class="caption">v0.3</text>',
    ]

    paired_bars(
        parts,
        x=100,
        label="Test automatici superati",
        old=old_tests,
        new=new_tests,
        y=285,
        maximum=220,
    )
    paired_bars(
        parts,
        x=100,
        label="Chiamate MCP host osservate",
        old=old_mcp,
        new=new_mcp,
        y=385,
        maximum=30,
    )
    paired_bars(
        parts,
        x=100,
        label="Esecuzioni native con receipt",
        old=old_runs,
        new=new_runs,
        y=485,
        maximum=4,
    )

    paired_bars(
        parts,
        x=850,
        label="Copertura istruzioni",
        old=old_coverage,
        new=new_coverage,
        y=285,
        maximum=100,
        suffix="%",
    )
    paired_bars(
        parts,
        x=850,
        label="Corpus reale preparato per eval",
        old=0,
        new=new["context_and_evaluation"]["real_corpus_skills"],
        y=385,
        maximum=110,
        suffix=" skill",
    )
    parts.extend(
        [
            text(850, 490, "A/B/C con giudizio umano", 21),
            text(
                850,
                525,
                "v0.2: non avviato     v0.3: non avviato (approvazione richiesta)",
                20,
                "#9f1239",
            ),
            text(
                850,
                555,
                "Non esiste quindi una misura comparabile di pertinenza per skill o subagent.",
                18,
                "#475569",
            ),
        ]
    )

    latency_rows = [
        ("p50", old_benchmark["p50_ms"], new_benchmark["search_p50_ms"]),
        ("p95", old_benchmark["p95_ms"], new_benchmark["search_p95_ms"]),
        ("massimo", old_benchmark["max_ms"], new_benchmark["search_max_ms"]),
    ]
    for index, (label, previous, current) in enumerate(latency_rows):
        y = 745 + index * 80
        paired_bars(
            parts,
            x=100,
            label=f"{label} (ms)",
            old=previous,
            new=current,
            y=y,
            maximum=135,
            suffix=" ms",
        )
        delta = (current / previous - 1) * 100
        parts.append(text(1250, y + 32, f"Δ {delta:+.1f}%", 20, "#9f1239"))

    parts.extend(
        [
            text(
                100,
                1038,
                "Lettura corretta: la robustezza verificata cresce (test, sessioni MCP e receipt); il nuovo benchmark non dimostra un guadagno di latenza.",
                20,
                "#0f172a",
            ),
            text(
                100,
                1072,
                "Fonti: doc/Capability_Verifica.json e doc/Capability_Verifica_v0.3.json. Generato il 19 ottobre 2026.",
                17,
                "#475569",
            ),
            "</svg>",
        ]
    )
    OUTPUT.write_text("\n".join(parts), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()

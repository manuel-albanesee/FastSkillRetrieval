"""Prepare a local fixture/config for a later direct Claude Code session; do not run it."""

import json
import sys
from pathlib import Path

from fastskillretrieval.catalog import Catalog
from fastskillretrieval.packages import safe_path

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = safe_path(ROOT / ".work" / "claude-test")
    work.mkdir(parents=True, exist_ok=True)
    state = safe_path(work / "state")
    source = safe_path(ROOT / "examples" / "skills")
    service = Catalog(state, source)
    try:
        item = service.call("inventory", "setup", refresh=True)["items"][0]
        if item["state"] not in {"validated", "published"}:
            metadata = json.loads((ROOT / "examples" / "markdown-candidate.json").read_text())
            result = service.call(
                "submit", "setup", skill_id=item["id"], version=item["version"], candidate=metadata
            )
            if result["status"] != "ok":
                raise RuntimeError(f"Fixture validation failed: {result['status']}")
        if service.call("publish", "setup")["status"] != "ok":
            raise RuntimeError("Fixture publication failed")
    finally:
        service.close()
    config = {
        "mcpServers": {
            "fastskillretrieval": {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    "-m",
                    "fastskillretrieval",
                    "--state",
                    str(state),
                    "--source-root",
                    str(source),
                    "serve",
                ],
            }
        }
    }
    destination = safe_path(work / "mcp-config.json")
    destination.write_text(json.dumps(config, indent=2), encoding="utf-8")
    prompt = (
        "Verifica FastSkillRetrieval esclusivamente con i suoi tool MCP. Usa session_id "
        "claude-functional-01 per tutte le chiamate di questa nuova conversazione. "
        "1) catalog_diagnostics: attesa modalita assistance. "
        "2) search_skills query Markdown: attesa una candidata. "
        "3) load_skill con il riferimento restituito: atteso content_emitted. "
        "4) read_skill_resource references/checklist.md: atteso marker FSR-MARKDOWN-CHECK-01. "
        "5) read_skill_resource ../outside.txt: atteso policy_denied. "
        "6) search_skills query nomatchunique98765: atteso no_match. "
        "Mostra gli esiti effettivi e gli strumenti invocati. Non modificare file, "
        "non eseguire script delle skill e non dichiarare verificata la riduzione del contesto "
        "o l'invocazione nativa: questa prova riguarda solo assistenza e trasporto MCP."
    )
    safe_path(work / "prompt.txt").write_text(prompt, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "config": str(destination),
                "prompt": str(work / "prompt.txt"),
                "claude_started": False,
            }
        )
    )


if __name__ == "__main__":
    main()

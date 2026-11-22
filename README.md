# FastSkillRetrieval

**Local, versioned capability retrieval for coding agents.**
Index skills, subagents and workflows once, then find the few that matter for a request
in milliseconds. No LLM calls, no network, no API keys.

![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![SQLite FTS5](https://img.shields.io/badge/search-SQLite%20FTS5%20%2F%20BM25-green)
![MCP stdio](https://img.shields.io/badge/interface-CLI%20%2B%20MCP%20stdio-lightgrey)

## Why

Agent setups accumulate hundreds of skills. Loading all their descriptions into every
session wastes context; guessing which one applies wastes turns. FastSkillRetrieval keeps
the catalog outside the prompt and returns a bounded shortlist on demand.

- **Deterministic.** SQLite FTS5 with BM25 and fixed field weights. The same query on the
  same catalog returns the same result.
- **Versioned and verifiable.** Every published capability is an immutable, hashed
  snapshot. A tampered package returns `stale_reference` instead of loading.
- **Budgeted.** Cards, load groups and per-session output have hard byte limits, so
  retrieval cannot flood the context.
- **Read-only on your sources.** Original skill folders are never modified, and the
  framework never executes scripts or models.
- **Two front ends.** A CLI and a `stdio` MCP server expose the same operations.

## How it works

```
 source root            registry (state dir)                 client
 skills/<name>/  -->  discover -> submit -> publish  -->  search / load / resource
 SKILL.md             validated metadata, snapshots,       CLI  or  MCP over stdio
                      FTS5 index, session budgets
```

1. `discover` scans an authorized source root (one skill per subfolder, each with `SKILL.md`).
2. `submit` attaches reviewed metadata: description, aliases, capabilities, use-when
   conditions, exclusions, technologies.
3. `publish` builds complete snapshots, then activates the catalog in one SQLite transaction.
4. `search` returns at most five capability cards ranked by BM25; `load` and `resource`
   emit the full instructions only for what was selected.

## Quick start

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen --extra dev
uv run python -m fastskillretrieval --help
```

Try it on the bundled example skills:

```bash
uv run python -m fastskillretrieval --source-root examples/skills --state .fsr \
  --session demo inventory --refresh
```

The step-by-step walkthrough (inspect, submit, publish, search, load) is in the
[full guide](doc/Guida_IT.md), currently written in Italian.

## Use it with your coding agent (Claude Code, Codex)

FastSkillRetrieval can index every skill, subagent and slash command you have installed and
suggest the relevant ones **before** your request reaches the model, through a read-only
`UserPromptSubmit` hook.

```bash
fastskillretrieval install --agent claude      # or: --agent codex
fastskillretrieval init-index --agent claude   # or run /fsr-init-index inside the agent
```

- `install` merges a hook entry into `~/.claude/settings.json` (Codex: `~/.codex/hooks.json`)
  and adds the `/fsr-init-index` command. It is idempotent, keeps a one-time `.fsr-backup` and
  never touches entries it did not create. `uninstall` removes only its own. Codex requires you
  to trust the new hook once from its `/hooks` screen.
- `init-index` discovers user, project and plugin skills, agents and commands (disabled plugins
  are skipped), stages copies under `~/.fsr/sources/<agent>/` and publishes the registry to
  `~/.fsr/index/<agent>/`. No model is called. Add `--if-stale` to rebuild only when the installed
  set changed. A full rebuild of about 570 items takes roughly three minutes.
- The hook opens the registry read-only (about 1 to 4 ms) and adds the best matches as context.
  `FSR_MODE=inject` (default), `suggest` (log only to `~/.fsr/suggestions.jsonl`) or `off`.
  `FSR_HOME` moves the whole `~/.fsr` folder.

Used by [Caronte](https://github.com/manuel-albanesee/Caronte), which installs it automatically.

## MCP server

```bash
uv run python -m fastskillretrieval serve
```

A configuration template is in [`examples/mcp-config.template.json`](examples/mcp-config.template.json).
The source root is fixed at startup and is never accepted from a tool call.

## Search behavior

- BM25 field weights: description 5, capabilities 4, use-when 4, aliases 3, technologies 2.
- Queries are turned into quoted literal terms, never interpreted as FTS syntax.
  `C`, `C++` and `C#` stay distinct.
- Exclusions are not indexed as positive signals. Negations in natural language are not
  understood: use explicit filters (`--technology`, `--exclude-id`, `--explicit-id`).
- Popularity and validation scores never influence ranking.
- Descriptions are matched literally, so queries should use the language of the indexed
  descriptions. Translate or enrich queries upstream when your catalog is in another language.

## Querying the index from other runtimes

The registry is a plain SQLite file. A read-only consumer can open `registry.sqlite3`
directly and run an FTS5 query, which avoids starting Python for every lookup. For example,
`node:sqlite` answers a query in about 1 to 4 ms on a catalog of roughly 480 entries.

## Windows notes

Snapshots are stored under `packages/<24 hex>/<64 hex>/<file>`. With the default 260-character
`MAX_PATH` a long state directory can break publication, so the registry now fails early
with `policy_denied` / `state_path_too_long`. Use a short state path such as `C:/fsr`.

## Development

```bash
uv sync --frozen --extra dev
uv run python -m pytest -q
uv run ruff check src tests
```

Current status: 335 passed, 1 skipped (the skipped test is platform-specific).

## Project layout

| Path | Contents |
|---|---|
| `src/fastskillretrieval/` | registry, catalog, ingestion, planner, CLI, MCP server |
| `tests/` | acceptance and regression tests |
| `schemas/` | JSON schemas for contracts |
| `examples/` | sample skills, candidate metadata, MCP config template |
| `scripts/` | benchmarks and verification scripts |
| `doc/` | design notes, evaluations and the Italian guide |
| `plans/` | implementation plans |

## Scope and limits

This is version 0.3. Byte counts used for budgets are a conservative estimate, not a
tokenizer count. The host verification matrix does not certify generalized preloading or
permission enforcement, and automatic planner dispatch is deliberately absent. See
[`doc/Capability_Host_Verification.md`](doc/Capability_Host_Verification.md).

## License

[MIT](LICENSE).

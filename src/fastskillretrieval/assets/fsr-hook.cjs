'use strict';
// FastSkillRetrieval prompt hook (Claude Code `UserPromptSubmit`, Codex `UserPromptSubmit`).
// Read-only: queries <FSR_HOME>/index/<agent>/registry.sqlite3 with node:sqlite (Node >= 22.5).
// Usage: node fsr-hook.cjs <agent>
// Env:   FSR_HOME (default ~/.fsr)   FSR_MODE = inject (default) | suggest (log only) | off
// Any problem results in no output: the hook never blocks or delays the prompt.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const STOP = new Set(`a ad agli al all alla alle allo anche che chi col come con da dai dal dall dalla dalle
dallo dei del dell della delle dello di due e è ed fra gia già gli ha hanno ho i il in l la
le lo ma mi ne nei nel nell nella nelle nello non o per perche perché piu più quale quali
quest se si sia sono su sui sul sull sulla sulle sullo ti tra un una uno cosi così
the of to and for with an is are was were be been on at by from in as or its this that
these those into about my your our their please can you i me it do does how what`.split(/\s+/));
const ELISION = /\b(?:c|l|d|dov|cos|com|quell|quest|dell|all|dall|nell|sull|un|anch)['’](?=[\p{L}\p{N}_])/gu;
const WEIGHTS = '0,5,4,4,3,2'; // capability_id, description, capabilities, use_when, aliases, technologies
const LIMITS = { threshold: -12, relative: 0.6, limit: 3, minTerms: 2 };

function queryTerms(text) {
  let v = String(text).normalize('NFC').toLowerCase().replace(ELISION, ' ');
  v = v.replace(/\bc\+\+/g, ' cpp ').replace(/\bc#/g, ' csharp ');
  const seen = new Set();
  for (const t of v.match(/[\p{L}\p{N}]+/gu) || []) if (!STOP.has(t)) seen.add(t);
  return [...seen].slice(0, 64);
}

function search(db, query) {
  const { threshold, relative, limit, minTerms } = LIMITS;
  const terms = queryTerms(query);
  if (terms.length < minTerms) return [];
  const expression = terms.map(t => `"${t.replaceAll('"', '')}"`).join(' OR ');
  const rows = db.prepare(
    `SELECT c.name AS name, c.kind AS kind, bm25(capability_fts,${WEIGHTS}) AS score
     FROM capability_fts f JOIN capabilities c ON c.id = f.capability_id
     WHERE capability_fts MATCH ? AND c.status = 'enabled'
     ORDER BY score LIMIT ?`).all(expression, limit * 3);
  const best = rows.length ? rows[0].score : 0;
  return rows.filter(r => r.score <= threshold && r.score <= best * relative).slice(0, limit);
}

function describe(dir, hit) {
  // Staged folders are named agent--<name> / cmd--<name>; sources.json maps them back to the real name.
  try {
    const map = JSON.parse(fs.readFileSync(path.join(dir, 'sources.json'), 'utf8'));
    const entry = map[hit.name];
    if (entry) return { name: entry.name, kind: entry.kind };
  } catch { /* fall through */ }
  return { name: hit.name.replace(/^(?:agent|cmd)--/, ''), kind: hit.kind };
}

function run(event, agent, env = process.env) {
  const mode = env.FSR_MODE || 'inject';
  if (mode === 'off' || typeof event.prompt !== 'string') return null;
  const home = env.FSR_HOME || path.join(os.homedir(), '.fsr');
  const dir = path.join(home, 'index', agent);
  const file = path.join(dir, 'registry.sqlite3');
  let db;
  try {
    if (!fs.existsSync(file)) return null;
    const { DatabaseSync } = require('node:sqlite');
    db = new DatabaseSync(file, { readOnly: true });
    const hits = search(db, event.prompt).map(h => describe(dir, h));
    if (mode === 'suggest') {
      fs.appendFileSync(path.join(home, 'suggestions.jsonl'),
        JSON.stringify({ at: new Date().toISOString(), agent, query: event.prompt.slice(0, 200), hits }) + '\n');
      return null;
    }
    if (!hits.length) return null;
    return {
      hookSpecificOutput: {
        hookEventName: 'UserPromptSubmit',
        additionalContext: 'FastSkillRetrieval: installed tools possibly relevant to this request (use only if they fit): '
          + hits.map(h => `${h.name} (${h.kind})`).join(', '),
      },
    };
  } catch { return null; } finally { try { db?.close(); } catch { /* ignore */ } }
}

function main() {
  let event;
  try { event = JSON.parse(fs.readFileSync(0, 'utf8').replace(/^﻿/, '')); } catch { return; }
  const out = run(event, process.argv[2] || 'claude');
  if (out) process.stdout.write(JSON.stringify(out));
}
if (require.main === module) main();
module.exports = { queryTerms, search, run };

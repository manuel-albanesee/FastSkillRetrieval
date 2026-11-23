'use strict';
// FastSkillRetrieval prompt hook (Claude Code `UserPromptSubmit`, Codex `UserPromptSubmit`).
// Queries <FSR_HOME>/index/<agent>/registry.sqlite3 read-only with node:sqlite (Node >= 22.5).
// Besides names, injects the SKILL.md text of the top hits from <FSR_HOME>/sources/<agent>/, once per session
// (the only state it writes: <FSR_HOME>/sessions/<session_id>.json).
// Usage: node fsr-hook.cjs <agent>
// Env:   FSR_HOME (default ~/.fsr)   FSR_MODE = inject (default) | suggest (log only) | off
//        FSR_INJECT_BODY = on (default) | off   FSR_BODY_HITS (default 2)   FSR_BODY_LIMIT bytes per skill (default 8000)
//        FSR_INJECT_LIMIT total bytes of injected context (default 12000, min 1000)
//        FSR_GATE_WAIT ms (default 2000)   FSR_GATE_LEASE ms (default 60000)
// Any problem results in no output: the hook never blocks the prompt.
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
     ORDER BY score, c.name LIMIT ?`).all(expression, limit * 3);
  const best = rows.length ? rows[0].score : 0;
  return rows.filter(r => r.score <= threshold && r.score <= best * relative).slice(0, limit);
}

function describe(dir, hit) {
  // Staged folders are named agent--<name> / cmd--<name>; sources.json maps them back to the real name.
  try {
    const map = JSON.parse(fs.readFileSync(path.join(dir, 'sources.json'), 'utf8'));
    const entry = map[hit.name];
    if (entry) return { name: entry.name, kind: entry.kind, staged: hit.name };
  } catch { /* fall through */ }
  return { name: hit.name.replace(/^(?:agent|cmd)--/, ''), kind: hit.kind, staged: hit.name };
}

const BODY_KINDS = new Set(['skill', 'command']);

function stripFrontmatter(text) {
  return text.replace(/^﻿/, '').replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '').replace(/\r\n?/g, '\n').trim();
}

// Paths stay absolute (the model reads them) but always use '/', so output does not depend on the platform.
function posix(p) { return String(p).replaceAll('\\', '/'); }

// Short, stable fingerprint of the index (signature written by init_index); empty when unavailable.
function indexTag(dir) {
  try {
    const sig = JSON.parse(fs.readFileSync(path.join(dir, 'index.json'), 'utf8')).signature;
    if (typeof sig === 'string' && /^[0-9a-f]{12,}$/i.test(sig)) return ` [index ${sig.slice(0, 12).toLowerCase()}]`;
  } catch { /* no signature */ }
  return '';
}

// Caps the whole additionalContext at `limit` bytes (cut on a UTF-8 boundary, fixed notice appended).
function capTotal(text, limit) {
  if (Buffer.byteLength(text, 'utf8') <= limit) return text;
  const notice = `\n[output truncated at ${limit} bytes]`;
  return clip(text, limit - Buffer.byteLength(notice, 'utf8')).text + notice;
}

// Frontmatter keys with a runtime effect that plain injected text cannot reproduce (permissions, model, forked
// subagent, hooks...). A skill declaring any of them must be invoked through the Skill tool, never hidden.
const RUNTIME_KEYS = new Set(['allowed-tools', 'disallowed-tools', 'model', 'effort', 'context', 'agent', 'hooks', 'background']);

function runtimeKeys(text) {
  const fm = /^﻿?---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (!fm) return [];
  return fm[1].split(/\r?\n/).map(l => /^([\w-]+):/.exec(l)?.[1]).filter(k => k && RUNTIME_KEYS.has(k));
}

function clip(text, limit) {
  const buf = Buffer.from(text, 'utf8');
  if (buf.length <= limit) return { text, cut: false };
  return { text: buf.subarray(0, limit).toString('utf8').replace(/�+$/, ''), cut: true };
}

function sessionFile(home, sessionId) {
  if (typeof sessionId !== 'string' || !/^[\w-]{1,128}$/.test(sessionId)) return null;
  return path.join(home, 'sessions', `${sessionId}.json`);
}

function readInjected(file) {
  if (!file) return new Set();
  try { return new Set(JSON.parse(fs.readFileSync(file, 'utf8'))); } catch { return new Set(); }
}

function writeInjected(file, set) {
  if (!file) return;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify([...set]));
  } catch { /* dedup is best effort */ }
}

// Full SKILL.md text (frontmatter removed, size-capped) for the top hits, once per session.
function bodies(home, agent, hits, event, env) {
  const native = new Set();
  const flag = String(env.FSR_INJECT_BODY ?? 'on').toLowerCase();
  if (['off', '0', 'false', 'no'].includes(flag)) return { out: [], native };
  const maxHits = Math.max(0, Number.parseInt(env.FSR_BODY_HITS ?? '2', 10) || 0);
  const limit = Math.max(500, Number.parseInt(env.FSR_BODY_LIMIT ?? '8000', 10) || 8000);
  const file = sessionFile(home, event.session_id);
  const seen = readInjected(file);
  const out = [];
  for (const [i, hit] of hits.entries()) {
    if (!BODY_KINDS.has(hit.kind)) continue;
    const skillDir = path.join(home, 'sources', agent, hit.staged);
    let raw;
    try { raw = fs.readFileSync(path.join(skillDir, 'SKILL.md'), 'utf8'); } catch { continue; }
    // Skills are hidden from Claude's own list (see hide_skills.py), so every one is injected here. Frontmatter
    // fields that only Claude Code could enforce are declared in the header instead.
    const runtime = runtimeKeys(raw);
    if (i >= maxHits || seen.has(hit.name)) continue;
    const body = stripFrontmatter(raw);
    if (!body) continue;
    const { text, cut } = clip(body, limit);
    out.push(`--- ${hit.kind} ${hit.name} (files in ${posix(skillDir)})`
      + (runtime.length ? ` [declared but not enforced: ${runtime.join(', ')}; stay within them]` : '') + ` ---\n${text}`
      + (cut ? `\n[truncated at ${limit} bytes; read ${posix(path.join(skillDir, 'SKILL.md'))} for the rest]` : ''));
    seen.add(hit.name);
  }
  if (out.length) writeInjected(file, seen);
  return { out, native };
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
        JSON.stringify({ at: new Date().toISOString(), agent, query: event.prompt.slice(0, 200),
          hits: hits.map(({ name, kind }) => ({ name, kind })) }) + '\n');
      return null;
    }
    if (!hits.length) return null;
    const note = h => (native.has(h.name) ? ', invoke with the Skill tool' : '');
    const { out: loaded, native } = bodies(home, agent, hits, event, env);
    let context = `FastSkillRetrieval${indexTag(dir)}: installed tools possibly relevant to this request (use only if they fit): `
      + hits.map(h => `${h.name} (${h.kind}${note(h)})`).join(', ');
    if (loaded.length) {
      context += '\nTheir instructions follow; apply them directly instead of calling the Skill tool.\n\n' + loaded.join('\n\n');
    }
    const injectLimit = Math.max(1000, Number.parseInt(env.FSR_INJECT_LIMIT ?? '12000', 10) || 12000);
    return { hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: capTotal(context, injectLimit) } };
  } catch { return null; } finally { try { db?.close(); } catch { /* ignore */ } }
}

function main() {
  let event;
  try { event = JSON.parse(fs.readFileSync(0, 'utf8').replace(/^﻿/, '')); } catch { return; }
  if (event.hook_event_name && event.hook_event_name !== 'UserPromptSubmit') return;
  const out = run(event, process.argv[2] || 'claude');
  if (out) process.stdout.write(JSON.stringify(out));
}
if (require.main === module) main();
module.exports = { queryTerms, search, run };

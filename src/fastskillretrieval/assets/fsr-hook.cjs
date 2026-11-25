'use strict';
// FastSkillRetrieval prompt hook (Claude Code `UserPromptSubmit`, Codex `UserPromptSubmit`).
// Queries <FSR_HOME>/index/<agent>/registry.sqlite3 read-only with node:sqlite (Node >= 22.5).
// Besides names, injects the SKILL.md text of the top hits from <FSR_HOME>/sources/<agent>/, once per session
// (the only state it writes: <FSR_HOME>/sessions/<session_id>.json).
// Usage: node fsr-hook.cjs <agent>
// Env:   FSR_HOME (default ~/.fsr)   FSR_MODE = inject (default) | suggest (log only) | off
//        FSR_INJECT_BODY = on (default) | off   FSR_BODY_HITS (default 2)   FSR_BODY_LIMIT bytes per skill (default 8000)
//        FSR_INJECT_LIMIT total bytes of injected context (default 12000, min 1000)
//        FSR_STRONG_COVERAGE share of request terms a hit must match for its text to be injected (default 0.3)
//        FSR_STRONG_CAP max request terms counted for that share (default 12)
//        FSR_UI = on (default) | off   user-facing banner (SessionStart) and per-prompt notices (systemMessage)
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
// A hit is "strong" (its full text may be injected) only when it matches at least `minMatched` distinct request
// terms covering `coverage` of them, counted against at most `cap` terms so long requests are not penalised.
// Weaker hits are still named: a false positive then costs a few tokens instead of a whole SKILL.md.
// Tuned with scripts/hook_eval.cjs (best body recall with no injection on the negative prompts).
const STRONG = { minMatched: 2, coverage: 0.3, cap: 12 };

function queryTerms(text) {
  let v = String(text).normalize('NFC').toLowerCase().replace(ELISION, ' ');
  v = v.replace(/\bc\+\+/g, ' cpp ').replace(/\bc#/g, ' csharp ');
  const seen = new Set();
  // Bare numbers ("7.3k tokens", "85 agents") match version strings in unrelated skills.
  for (const t of v.match(/[\p{L}\p{N}]+/gu) || []) if (!STOP.has(t) && !/^\p{N}+$/u.test(t)) seen.add(t);
  return [...seen].slice(0, 64);
}

// Italian request words -> English index terms (the indexed descriptions are mostly English; FTS5 has no stemming
// or translation). A key ending in '*' is a prefix, otherwise an exact word. Deterministic, no model involved.
const EXPAND = new Map(Object.entries({
  'semplific*': 'simplify simplifier simplification', 'semplice': 'simple simplify', 'complicat*': 'complex complicated',
  'progett*': 'design architect', 'architettur*': 'architecture design architect',
  'rete reti': 'network connectivity', 'connettivit*': 'connectivity network troubleshoot',
  'diagnos* diagnostic*': 'diagnose troubleshoot diagnostic', 'libreria librerie': 'library libraries docs',
  'lent*': 'slow performance', 'prestazion*': 'performance slow optimize', 'bottiglia': 'bottleneck performance',
  'colli': 'bottleneck', 'compil*': 'build compile compilation', 'errore errori': 'error errors fix',
  'pulisc* pulizi*': 'clean cleanup', 'mort* inutilizz*': 'dead unused cleanup',
  'spieg*': 'explain understand', 'esistent*': 'existing codebase', 'esplor*': 'explore codebase',
  'memoria': 'memory', 'caricament*': 'loading load', 'carico': 'load',
  'sicurezza': 'security vulnerability', 'vulnerabil*': 'vulnerability security', 'segret*': 'secret secrets security',
  'revision*': 'review', 'rivedi rivedere': 'review', 'controll*': 'review check',
  'ottimizz*': 'optimize optimization performance', 'ricerca': 'search find', 'cerc*': 'search find',
  'trov* trovare': 'find search', 'flusso flussi': 'flow trace', 'traccia': 'trace flow',
  'sessione sessioni': 'session sessions', 'dipendenz*': 'dependencies dependency', 'documentazion*': 'documentation docs',
  'pianific*': 'plan planning', 'piano piani': 'plan planning', 'ristruttur*': 'refactor refactoring',
  'implement*': 'implement implementation', 'aggiorn*': 'update updated latest', 'codice': 'code',
  'comportament*': 'behavior behaviour', 'notific*': 'notification notifications', 'servizio servizi': 'service services',
  'sito': 'website web', 'progetto': 'project', 'guidat*': 'driven tdd', 'sviluppo': 'development develop',
  'correggi correggere': 'fix correct', 'fallisce fallire': 'fail failing failure',
  'esempio esempi': 'example examples', 'migliora*': 'improve', 'sicuro': 'secure safe',
  'dati': 'data', 'database': 'database db', 'distribuzion*': 'deploy deployment', 'rilascio': 'release deploy',
}).flatMap(([keys, en]) => keys.split(' ').map(k => [k, en.split(' ')])));
const EXPAND_CAP = 24;

// English terms to add for the Italian words of a request; never duplicates or terms already in the request.
function expandTerms(terms) {
  const present = new Set(terms);
  const added = [];
  for (const t of terms) {
    for (const [key, en] of EXPAND) {
      const hit = key.endsWith('*') ? t.startsWith(key.slice(0, -1)) : t === key;
      if (!hit) continue;
      for (const e of en) {
        if (present.has(e)) continue;
        present.add(e);
        added.push(e);
      }
    }
  }
  return added.slice(0, EXPAND_CAP);
}

function quoted(term) { return `"${term.replaceAll('"', '')}"`; }

// Agents are named with a one-line description, so they cost little: two extra slots are reserved for the best
// agent hits, ranked among agents only. Otherwise a skill or command that scores far higher on the same words
// pushes every agent out of the shared top-3 by the relative cut.
const AGENT_SLOTS = 2;

function search(db, query) {
  const { threshold, relative, limit, minTerms } = LIMITS;
  const terms = queryTerms(query);
  if (terms.length < minTerms) return [];
  // Expansion only widens the FTS query; coverage (`matched`/`terms` below) stays on the request's own terms.
  const match = terms.concat(expandTerms(terms)).map(quoted).join(' OR ');
  const run = (only, n) => db.prepare(
    `SELECT f.capability_id AS id, c.name AS name, c.kind AS kind, bm25(capability_fts,${WEIGHTS}) AS score
     FROM capability_fts f JOIN capabilities c ON c.id = f.capability_id
     WHERE capability_fts MATCH ? AND c.status = 'enabled'${only ? " AND c.name LIKE 'agent--%'" : ''}
     ORDER BY score, c.name LIMIT ?`).all(match, n);
  const keep = rows => rows.filter(r => r.score <= threshold && r.score <= rows[0].score * relative);
  const hits = keep(run(false, limit * 3)).slice(0, limit);
  const named = new Set(hits.map(h => h.id));
  const have = hits.filter(h => h.name.startsWith('agent--')).length;
  for (const r of keep(run(true, AGENT_SLOTS * 3)).filter(r => !named.has(r.id)).slice(0, Math.max(0, AGENT_SLOTS - have))) hits.push(r);
  const probe = db.prepare('SELECT 1 FROM capability_fts WHERE capability_fts MATCH ? AND capability_id = ?');
  for (const hit of hits) {
    hit.matched = terms.filter(t => probe.get(quoted(t), hit.id)).length;
    hit.terms = terms.length;
  }
  return hits;
}

function strong(hit, env = {}) {
  const coverage = Number.parseFloat(env.FSR_STRONG_COVERAGE ?? '');
  const cap = Number.parseInt(env.FSR_STRONG_CAP ?? '', 10);
  const need = Number.isFinite(coverage) ? coverage : STRONG.coverage;
  return hit.matched >= STRONG.minMatched
    && hit.matched / Math.min(hit.terms, cap > 0 ? cap : STRONG.cap) >= need;
}

function describe(dir, hit) {
  // Staged folders are named agent--<name> / cmd--<name>; sources.json maps them back to the real name.
  try {
    const map = JSON.parse(fs.readFileSync(path.join(dir, 'sources.json'), 'utf8'));
    const entry = map[hit.name];
    if (entry) return { name: entry.name, kind: entry.kind, staged: hit.name, matched: hit.matched, terms: hit.terms };
  } catch { /* fall through */ }
  return { name: hit.name.replace(/^(?:agent|cmd)--/, ''), kind: hit.kind, staged: hit.name, matched: hit.matched,
    terms: hit.terms };
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

// One-line `description` from an agent's staged definition (never its body: Claude Code loads that only into the
// subagent). Needed when the agent listing is compacted to names (fsr-agent-listing mod). '' when unavailable.
function frontmatterField(raw, key) {
  const fm = /^﻿?---\r?\n([\s\S]*?)\r?\n---/.exec(raw);
  if (!fm) return '';
  const lines = fm[1].split(/\r?\n/);
  const at = lines.findIndex(l => l.startsWith(`${key}:`));
  if (at < 0) return '';
  let value = lines[at].slice(key.length + 1);
  if (/^\s*[|>][-+]?\s*$/.test(value)) {  // YAML block scalar: the indented lines that follow
    const block = [];
    for (const l of lines.slice(at + 1)) { if (!/^\s+\S/.test(l)) break; block.push(l.trim()); }
    value = block.join(' ');
  }
  return value.trim().replace(/^(['"])([\s\S]*)\1$/, '$2').replace(/\s+/g, ' ');
}

function agentDescription(home, agent, hit) {
  let raw;
  try { raw = fs.readFileSync(path.join(home, 'sources', agent, hit.staged, 'SKILL.md'), 'utf8'); } catch { return ''; }
  return clip(frontmatterField(raw, 'description'), 300).text;
}

// Each agent is described once per session (key `agent:<name>` in the session file shared with bodies()):
// the line stays in the conversation, so repeating it would only add tokens.
function agentBlock(home, agent, hits, event, env) {
  const flag = String(env.FSR_AGENT_DESC ?? 'on').toLowerCase();
  if (['off', '0', 'false', 'no'].includes(flag)) return { text: '', names: [] };
  const file = sessionFile(home, event.session_id);
  const seen = readInjected(file);
  const rows = hits.filter(h => h.kind === 'agent' && !seen.has(`agent:${h.name}`))
    .map(h => [h.name, agentDescription(home, agent, h)]).filter(([, d]) => d);
  if (!rows.length) return { text: '', names: [] };
  for (const [n] of rows) seen.add(`agent:${n}`);
  writeInjected(file, seen);
  return {
    text: '\nAgents for this request (Agent tool, subagent_type = name):\n' + rows.map(([n, d]) => `- ${n}: ${d}`).join('\n'),
    names: rows.map(([n]) => n),
  };
}

// Full SKILL.md text (frontmatter removed, size-capped) for the top hits, once per session.
function bodies(home, agent, hits, event, env) {
  const native = new Set();
  const flag = String(env.FSR_INJECT_BODY ?? 'on').toLowerCase();
  if (['off', '0', 'false', 'no'].includes(flag)) return { out: [], native, injected: [] };
  const injected = [];
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
    if (i >= maxHits || seen.has(hit.name) || !strong(hit, env)) continue;
    const body = stripFrontmatter(raw);
    if (!body) continue;
    const { text, cut } = clip(body, limit);
    out.push(`--- ${hit.kind} ${hit.name} (files in ${posix(skillDir)})`
      + (runtime.length ? ` [declared but not enforced: ${runtime.join(', ')}; stay within them]` : '') + ` ---\n${text}`
      + (cut ? `\n[truncated at ${limit} bytes; read ${posix(path.join(skillDir, 'SKILL.md'))} for the rest]` : ''));
    seen.add(hit.name);
    injected.push({ name: hit.name.replace(/^(?:agent|cmd)--/, ''), kind: hit.kind, tokens: tokens(text) });
  }
  if (out.length) writeInjected(file, seen);
  return { out, native, injected };
}

// ---- User-facing feedback -------------------------------------------------------------------------------------
// `systemMessage` is shown to the user by Claude Code and never added to the model's context, so it costs no tokens.
// Token counts are estimates (4 bytes per token), always printed with "~".
const tokens = text => Math.ceil(Buffer.byteLength(String(text), 'utf8') / 4);
const compactNum = n => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
const uiOn = env => !['off', '0', 'false', 'no'].includes(String(env.FSR_UI ?? 'on').toLowerCase());

function boxed(title, lines) {
  const width = Math.max(title.length + 4, ...lines.map(l => [...l].length)) + 2;
  const top = `╭─ ${title} ${'─'.repeat(Math.max(1, width - title.length - 3))}╮`;
  const row = l => `│ ${l}${' '.repeat(width - [...l].length - 1)}│`;
  return [top, ...lines.map(row), `╰${'─'.repeat(width)}╯`].join('\n');
}

function lastJsonLine(file) {
  try {
    const rows = fs.readFileSync(file, 'utf8').trim().split('\n');
    return JSON.parse(rows[rows.length - 1]);
  } catch { return null; }
}

// What the hidden skills/commands would cost in Claude's resident list: "- name: description" per entry.
function hiddenSkills(home) {
  const store = path.join(home, 'hidden-skills');
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(path.join(store, 'manifest.json'), 'utf8')); } catch { return { count: 0, tokens: 0 }; }
  let chars = 0;
  const seen = new Set();
  for (const [key, file] of Object.entries(manifest)) {
    // The manifest also holds files of plugin versions that are no longer installed: count live ones, once per name.
    if (!fs.existsSync(String(file))) continue;
    let raw;
    try { raw = fs.readFileSync(path.join(store, `${key}.orig`), 'utf8'); } catch { continue; }
    const base = path.basename(String(file));
    const name = frontmatterField(raw, 'name') || (/^skill\.md$/i.test(base) ? path.basename(path.dirname(String(file))) : base.replace(/\.md$/i, ''));
    if (seen.has(name)) continue;
    seen.add(name);
    chars += name.length + Math.min(frontmatterField(raw, 'description').length, 250) + 4;
  }
  const count = seen.size;
  return { count, tokens: Math.ceil(chars / 4) };
}

function sessionBanner(home, agent, dir, db, env) {
  // Agents and commands are indexed as staged skills: sources.json maps each folder back to its real kind.
  const kinds = { skill: 0, agent: 0, command: 0 };
  try {
    const map = JSON.parse(fs.readFileSync(path.join(dir, 'sources.json'), 'utf8'));
    for (const e of Object.values(map)) if (e && e.kind in kinds) kinds[e.kind]++;
    const total = db.prepare("SELECT COUNT(*) AS n FROM capabilities WHERE status = 'enabled'").get().n;
    kinds.skill = Math.max(0, total - kinds.agent - kinds.command);
  } catch { /* counts stay 0 */ }
  const listing = agent === 'claude' ? lastJsonLine(path.join(home, 'mods', 'fsr-agent-listing', 'listing.jsonl')) : null;
  const agents = listing && listing.chars_before > listing.chars_after ? Math.ceil((listing.chars_before - listing.chars_after) / 4) : 0;
  const skills = hiddenSkills(home);
  const total = agents + skills.tokens;
  const lines = [
    `index ${indexTag(dir).replace(/^ \[index |\]$/g, '') || 'n/a'} · ${kinds.skill ?? 0} skills · ${kinds.agent ?? 0} agents · ${kinds.command ?? 0} commands`,
    total ? `saved ~${compactNum(total)} tokens on every turn:` : 'no resident context hidden yet (see hide-skills / agent listing mod)',
  ];
  if (agents) lines.push(`  ▸ agent listing   -${compactNum(agents)}  (${listing.compacted} agents left out)`);
  if (skills.tokens) lines.push(`  ▸ skills/commands -${compactNum(skills.tokens)}  (${skills.count} hidden)`);
  lines.push('relevant ones are injected on demand, marked "◆ FSR"');
  return boxed('◆ FSR · FastSkillRetrieval active', lines);
}

function promptNotice(loaded, agentNames, hits) {
  const lines = [];
  for (const s of loaded) lines.push(`◆ FSR ▸ ${s.kind} injected: ${s.name} (~${compactNum(s.tokens)} tokens)`);
  if (agentNames.length) lines.push(`◆ FSR ▸ agent${agentNames.length > 1 ? 's' : ''} found: ${agentNames.join(', ')}`);
  const shown = new Set([...loaded.map(s => s.name), ...agentNames]);
  const rest = hits.filter(h => !shown.has(h.name)).map(h => h.name);
  if (lines.length && rest.length) lines.push(`◆ FSR ▸ also suggested: ${rest.join(', ')}`);
  return lines.join('\n');
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
    const { out: loaded, native, injected } = bodies(home, agent, hits, event, env);
    let context = `FastSkillRetrieval${indexTag(dir)}: installed tools possibly relevant to this request (use only if they fit): `
      + hits.map(h => `${h.name} (${h.kind}${note(h)})`).join(', ');
    const described = agentBlock(home, agent, hits, event, env);
    context += described.text;
    if (loaded.length) {
      context += '\nTheir instructions follow; apply them directly instead of calling the Skill tool.\n\n' + loaded.join('\n\n');
    }
    const injectLimit = Math.max(1000, Number.parseInt(env.FSR_INJECT_LIMIT ?? '12000', 10) || 12000);
    const out = { hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: capTotal(context, injectLimit) } };
    const notice = uiOn(env) ? promptNotice(injected, described.names, hits) : '';
    if (notice) out.systemMessage = notice;
    return out;
  } catch { return null; } finally { try { db?.close(); } catch { /* ignore */ } }
}

// SessionStart: a banner for the user only (systemMessage), nothing is added to the model's context.
function start(event, agent, env = process.env) {
  if (!uiOn(env) || (env.FSR_MODE || 'inject') === 'off' || event.source === 'compact') return null;
  const home = env.FSR_HOME || path.join(os.homedir(), '.fsr');
  const dir = path.join(home, 'index', agent);
  const file = path.join(dir, 'registry.sqlite3');
  let db;
  try {
    if (!fs.existsSync(file)) return null;
    const { DatabaseSync } = require('node:sqlite');
    db = new DatabaseSync(file, { readOnly: true });
    // Claude Code prefixes "SessionStart:<source> says: " on the same line and trims leading whitespace, which
    // would shift the box's first row. A plain first line takes the prefix; the box then starts at column 0.
    return { systemMessage: 'FastSkillRetrieval ready:\n' + sessionBanner(home, agent, dir, db, env) };
  } catch { return null; } finally { try { db?.close(); } catch { /* ignore */ } }
}

// `install` leaves a marker file; the first session or prompt afterwards tells the user, once, what to do next.
// The text goes out as `systemMessage`, so it reaches the user and never the model's context.
function installNotice(agent, env = process.env) {
  if ((env.FSR_MODE || 'inject') === 'off') return null;
  const marker = path.join(env.FSR_HOME || path.join(os.homedir(), '.fsr'), `installed-${agent}`);
  try {
    if (!fs.existsSync(marker)) return null;
    fs.unlinkSync(marker);
    return 'FastSkillRetrieval is now installed in this agent.\n'
      + 'Run /fsr-init-index once to index your skills, agents and commands: it enables the automatic, '
      + 'per-request injection of the right agents and skills. Re-run it after installing new plugins.';
  } catch { return null; }
}

function main() {
  let event;
  try { event = JSON.parse(fs.readFileSync(0, 'utf8').replace(/^﻿/, '')); } catch { return; }
  const agent = process.argv[2] || 'claude';
  let out = null;
  if (event.hook_event_name === 'SessionStart') out = start(event, agent);
  else if (!event.hook_event_name || event.hook_event_name === 'UserPromptSubmit') out = run(event, agent);
  const note = installNotice(agent);
  if (note) out = { ...out, systemMessage: out && out.systemMessage ? `${note}\n\n${out.systemMessage}` : note };
  if (out) process.stdout.write(JSON.stringify(out));
}
if (require.main === module) main();
module.exports = { queryTerms, expandTerms, search, strong, run, start, STRONG };

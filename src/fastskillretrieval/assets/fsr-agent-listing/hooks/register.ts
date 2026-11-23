import type { Register } from 'claude-code'

// Rewrites the text of Claude Code's `agent_listing_delta` attachment. Every agent stays offered and
// dispatchable (no agent.offer hook): an agent missing from the text can still be launched by its name.
//   mode "builtin" (default): only the KEEP agents keep their entry; the others are left out and
//     FastSkillRetrieval's prompt hook names the relevant ones, with their description, once per session.
//   mode "names": the others are listed by bare name, grouped by plugin prefix.
// Settings: <plugin root>/config.json, e.g. { "mode": "names", "keep": ["ecc:code-reviewer"] }.
// The engine holds the answer per attachment for the process, so the rewritten text is stable (prompt cache).

export type Mode = 'builtin' | 'names'
export const KEEP = ['claude', 'general-purpose', 'Explore', 'Plan', 'claude-code-guide']
const LINE = /^- (\S+?): (.*)$/ // names may contain ':' (ecc:architect) but never ': '
export const NOTE: Record<Mode, string> = {
  builtin:
    'Other installed agents are not listed here. FastSkillRetrieval names the ones relevant to each request, with ' +
    'their description; any of those names, or one the user gives, is a valid subagent_type.',
  names:
    'Most agents are listed by name only. FastSkillRetrieval adds the description of the agents relevant to each ' +
    'request; any name listed here is a valid subagent_type ("<group>:<name>" for grouped ones).',
}

export function compact(text: string, keep: Set<string>, mode: Mode = 'builtin') {
  const out: string[] = []
  const groups = new Map<string, string[]>()
  let compacted = 0
  let kept = 0
  let pending = false // entries removed since the last note
  let dropping = false // inside a removed entry: its continuation lines go too
  const flush = () => {
    if (!pending) return
    for (const [prefix, names] of groups) out.push(prefix ? `- ${prefix}: ${names.join(', ')}` : `- ${names.join(', ')}`)
    groups.clear()
    out.push(NOTE[mode])
    pending = false
  }
  for (const line of text.split('\n')) {
    const m = LINE.exec(line)
    if (!m) {
      if (dropping && line.trim() && !line.startsWith('- ')) continue
      dropping = false
      flush()
      out.push(line)
      continue
    }
    const name = m[1] ?? ''
    if (keep.has(name)) { dropping = false; out.push(line); kept++; continue }
    compacted++
    pending = true
    dropping = true
    if (mode === 'builtin') continue
    const cut = name.indexOf(':')
    const prefix = cut > 0 ? name.slice(0, cut) : ''
    if (!groups.has(prefix)) groups.set(prefix, [])
    groups.get(prefix)!.push(cut > 0 ? name.slice(cut + 1) : name)
  }
  flush()
  return { text: out.join('\n'), compacted, kept }
}

const rows: string[] = []
let flushing: Promise<void> = Promise.resolve()
function log($: any, row: Record<string, unknown>) {
  rows.push(JSON.stringify({ at: new Date().toISOString(), ...row }))
  const text = rows.slice(-500).join('\n') + '\n'
  flushing = flushing.then(() => $.fs.write(`${$.plugin.root}/listing.jsonl`, text)).catch(() => {})
}

type Settings = { mode: Mode; keep: Set<string> }
export function settingsFrom(raw: unknown): Settings {
  const c = (raw && typeof raw === 'object' ? raw : {}) as { mode?: unknown; keep?: unknown }
  const extra = Array.isArray(c.keep) ? c.keep.filter((k): k is string => typeof k === 'string') : []
  return { mode: c.mode === 'names' ? 'names' : 'builtin', keep: new Set([...KEEP, ...extra]) }
}

let settings: Promise<Settings> | undefined
function load($: any): Promise<Settings> {
  settings ??= $.fs.read(`${$.plugin.root}/config.json`)
    .then((t: string) => settingsFrom(JSON.parse(t)))
    .catch(() => settingsFrom({}))
  return settings!
}

export const register: Register = on => {
  on('prompt.attachment', { type: 'agent_listing_delta' }, async ($, e, next) => {
    const result = await next(e)
    if (typeof result.text !== 'string') return result
    const { mode, keep } = await load($)
    const done = compact(result.text, keep, mode)
    log($, { mode, chars_before: result.text.length, chars_after: done.text.length, compacted: done.compacted, kept: done.kept })
    return done.compacted ? { ...result, text: done.text } : result
  }).catch(($, e, next) => next(e))
}

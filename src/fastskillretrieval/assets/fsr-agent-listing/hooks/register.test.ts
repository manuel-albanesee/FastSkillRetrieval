import { expect, test } from 'claude-code/testing'
import { compact, KEEP, NOTE, settingsFrom } from './register'

const LISTING = [
  'Available agent types for the Agent tool:',
  '- general-purpose: General agent. (Tools: *)',
  '- ricerca-haiku: Read-only search. (Tools: Glob, Grep, Read)',
  '- ecc:architect: Designs systems',
  '  across several lines.',
  '- ecc:planner: Plans work.',
  '',
  'This is ambient context.',
].join('\n')

const keep = new Set(KEEP)

test('builtin mode keeps only the kept agents and adds the note once', async () => {
  const done = compact(LISTING, keep, 'builtin')
  expect(done.text).toBe([
    'Available agent types for the Agent tool:',
    '- general-purpose: General agent. (Tools: *)',
    NOTE.builtin,
    '',
    'This is ambient context.',
  ].join('\n'))
  expect(done.compacted).toBe(3)
  expect(done.kept).toBe(1)
})

test('names mode groups bare names by plugin prefix and drops continuation lines', async () => {
  const done = compact(LISTING, keep, 'names')
  expect(done.text).toBe([
    'Available agent types for the Agent tool:',
    '- general-purpose: General agent. (Tools: *)',
    '- ricerca-haiku',
    '- ecc: architect, planner',
    NOTE.names,
    '',
    'This is ambient context.',
  ].join('\n'))
})

test('a removal delta has no described entries and is left unchanged', async () => {
  const removal = 'The following agent types are no longer available:\n- vercel:ai-architect\n\nThis is ambient context.'
  const done = compact(removal, keep, 'builtin')
  expect(done.compacted).toBe(0)
  expect(done.text).toBe(removal)
})

test('config.json adds kept agents and picks the mode; anything invalid falls back to builtin', async () => {
  const s = settingsFrom({ mode: 'names', keep: ['ecc:planner', 3] })
  expect(s.mode).toBe('names')
  expect(s.keep.has('ecc:planner')).toBe(true)
  expect(s.keep.has('general-purpose')).toBe(true)
  expect(settingsFrom('nope').mode).toBe('builtin')
  const kept = compact(LISTING, s.keep, s.mode)
  expect(kept.text.includes('- ecc:planner: Plans work.')).toBe(true)
})

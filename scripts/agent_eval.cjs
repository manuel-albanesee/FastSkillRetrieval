'use strict';
// Measures how well the prompt hook names the right agent on labelled prompts, against a real FSR index
// (read-only).
// Usage: node scripts/agent_eval.cjs [cases.json] [--home <FSR_HOME>] [--agent claude] [--verbose]
//   recall@3        positive cases where at least one expected agent is among the (max 3) named hits
//   top-1           positive cases where the first named agent is an expected one
//   false positives negative cases (expect: []) where any agent is named
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');
const hook = require('../src/fastskillretrieval/assets/fsr-hook.cjs');

const args = process.argv.slice(2);
const flag = name => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : undefined; };
const verbose = args.includes('--verbose') && args.splice(args.indexOf('--verbose'), 1);
const home = flag('--home') || process.env.FSR_HOME || path.join(os.homedir(), '.fsr');
const agent = flag('--agent') || 'claude';
const casesFile = args[0] || path.join(__dirname, 'agent_eval_cases.json');
const dbFile = path.join(home, 'index', agent, 'registry.sqlite3');
if (!fs.existsSync(dbFile)) {
  console.error(`No index at ${dbFile}. Run init-index first.`);
  process.exit(1);
}
const { cases } = JSON.parse(fs.readFileSync(casesFile, 'utf8'));
const db = new DatabaseSync(dbFile, { readOnly: true });

const runs = cases.map(c => ({
  ...c,
  named: hook.search(db, c.prompt).filter(h => h.name.startsWith('agent--')).slice(0, 3).map(h => h.name.replace(/--plugin-.*$/, '')),
}));
const positives = runs.filter(r => r.expect.length);
const negatives = runs.filter(r => !r.expect.length);
const hit = r => r.named.some(n => r.expect.includes(n));
const recall = positives.filter(hit).length;
const top1 = positives.filter(r => r.named.length && r.expect.includes(r.named[0])).length;
const falsePositives = negatives.filter(r => r.named.length).length;

if (verbose) {
  for (const r of positives.filter(r => !hit(r))) {
    console.log(`MISS ${r.prompt.slice(0, 90)}\n  expected ${r.expect.join(', ')}\n  got      ${r.named.join(', ') || '(none)'}`);
  }
  for (const r of negatives.filter(r => r.named.length)) {
    console.log(`FALSE POSITIVE ${r.prompt.slice(0, 90)}\n  got ${r.named.join(', ')}`);
  }
}
console.log(`cases ${runs.length} (positive ${positives.length}, negative ${negatives.length})`);
console.log(`recall@3 ${recall}/${positives.length}  top-1 ${top1}/${positives.length}  false positives ${falsePositives}/${negatives.length}`);
db.close();

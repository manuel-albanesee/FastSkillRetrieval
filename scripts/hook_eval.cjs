'use strict';
// Measures the prompt hook's retrieval on labelled prompts against a real FSR index (read-only).
// Usage: node scripts/hook_eval.cjs [cases.json] [--home <FSR_HOME>] [--agent claude] [--verbose]
// For each strong-coverage threshold it reports:
//   named   positive cases whose expected tool is among the named hits (threshold-independent)
//   body    positive cases whose expected tool would have its full text injected (strong, within FSR_BODY_HITS=2)
//   waste   injected bodies that are not expected (every body injected for a negative case counts)
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
const casesFile = args[0] || path.join(__dirname, 'hook_eval_cases.json');
const { cases } = JSON.parse(fs.readFileSync(casesFile, 'utf8'));
const db = new DatabaseSync(path.join(home, 'index', agent, 'registry.sqlite3'), { readOnly: true });

const runs = cases.map(c => ({ ...c, hits: hook.search(db, c.prompt) }));
const positives = runs.filter(r => r.expect.length);
const named = positives.filter(r => r.hits.some(h => r.expect.includes(h.name))).length;

if (verbose) {
  for (const r of runs) {
    console.log(`\n${r.expect.length ? '+' : '-'} ${r.prompt.slice(0, 90)}`);
    for (const h of r.hits) {
      const mark = r.expect.includes(h.name) ? 'ok ' : '   ';
      console.log(`  ${mark}${h.score.toFixed(1).padStart(6)}  ${String(h.matched).padStart(2)}/${h.terms}  ${h.name}`);
    }
  }
  console.log('');
}
console.log(`cases ${runs.length} (positive ${positives.length})  named ${named}/${positives.length}`);
console.log('cap  coverage  body  waste  negatives-hit');
for (const cap of [8, 10, 12, 15, 64]) {
  for (const coverage of [0.2, 0.25, 0.3, 0.35, 0.4, 0.5]) {
    const env = { FSR_STRONG_COVERAGE: String(coverage), FSR_STRONG_CAP: String(cap) };
    let body = 0, waste = 0, negatives = 0;
    for (const r of runs) {
      const injected = r.hits.slice(0, 2).filter(h => hook.strong(h, env));
      if (r.expect.length && injected.some(h => r.expect.includes(h.name))) body++;
      // Agents only ever get a one-line description, so only skill/command bodies count as waste.
      const wasted = injected.filter(h => !r.expect.includes(h.name) && !h.name.startsWith('agent--')).length;
      waste += wasted;
      if (!r.expect.length && wasted) negatives++;
    }
    const mark = cap === hook.STRONG.cap && coverage === hook.STRONG.coverage ? '   <- default' : '';
    console.log(`${String(cap).padStart(3)}  ${coverage.toFixed(2).padStart(8)}  ${String(body).padStart(4)}`
      + `  ${String(waste).padStart(5)}  ${String(negatives).padStart(13)}${mark}`);
  }
}
db.close();

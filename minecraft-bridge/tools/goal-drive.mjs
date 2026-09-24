// Drive the running bridge toward one goal with a rule selector (no Python, no models).
//
//   node tools/goal-drive.mjs '{"predicate":"have","item":"planks","count":4}' [maxSteps]
//
// Rule: flee a reachable threat when unarmed, low on health or facing a creeper, else attack it;
// eat when hungry; otherwise the nearest goal candidate (waiting last). Checks the bridge without
// the Python side, and serves as the baseline to compare the Jev selector with.

const BRIDGE = 'http://127.0.0.1:3000'
const spec = JSON.parse(process.argv[2])
const maxSteps = Number(process.argv[3] ?? 40)

async function call (method, path, body) {
  const res = await fetch(`${BRIDGE}${path}`, { method, headers: { 'content-type': 'application/json' }, body: body && JSON.stringify(body) })
  const data = await res.json()
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}: ${JSON.stringify(data)}`)
  return data
}

function choose (view) {
  const cs = view.candidates
  const self = view.observation.self
  const threat = cs.find((c) => c.verb === 'attack' && c.hostile)
  if (threat) {
    const run = threat.weapon.startsWith('none') || self.health <= 8 || threat.target === 'creeper'
    // A goal's attack (cleared) comes without a flee: fight then
    return (run && cs.find((c) => c.verb === 'flee' && c.target === threat.target)) || threat
  }
  if (self.food <= 6) {
    const eat = cs.find((c) => c.verb === 'eat')
    if (eat) return eat
  }
  const work = cs.filter((c) => c.verb !== 'wait' && c.verb !== 'equip' && !(c.verb === 'eat'))
  work.sort((a, b) => (a.distance ?? 0) - (b.distance ?? 0))
  return work[0] ?? cs.find((c) => c.verb === 'wait')
}

const status = await call('PUT', '/goal', spec)
console.log('goal set:', JSON.stringify(status))
const t0 = Date.now()
for (let step = 1; step <= maxSteps; step++) {
  const view = await call('GET', '/observe')
  if (view.busy) { await new Promise((resolve) => setTimeout(resolve, 1000)); step--; continue }
  const g = view.goal
  console.log(`\n[${step}] remaining=${g.remaining} needs=${view.needs.join('; ')}`)
  console.log('  ' + g.lines.join('\n  '))
  if (g.blocked.length) console.log('  blocked:', g.blocked.join('; '))
  if (g.met) { console.log(`goal met after ${step - 1} steps, ${Math.round((Date.now() - t0) / 1000)}s`); break }
  console.log(`  candidates (${view.candidates.length}): ${view.candidates.map((c) => c.id).join(' | ')}`)
  const c = choose(view)
  const r = await call('POST', '/act', { id: c.id })
  console.log(`  -> ${c.id}: ${r.ok ? 'ok' : 'FAILED'} ${r.result} (${r.seconds}s)`)
}

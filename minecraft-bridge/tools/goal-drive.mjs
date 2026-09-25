// 動いているブリッジを、規則で選ぶ側を使って1つの目標に向けて動かす（Python もモデルも使わない）。
//
//   node tools/goal-drive.mjs '{"predicate":"have","item":"planks","count":4}' [maxSteps]
//
// 規則: 届く脅威には、武器がない、体力が低い、相手がクリーパーのどれかなら逃げ、そうでなければ
// 攻撃する。空腹なら食べる。それ以外は一番近い目標の候補（待つのは最後）。Python 側なしで
// ブリッジを確かめ、Jev の選択と比べる基準にもなる。

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
    // 目標の攻撃（cleared）には逃走の候補がつかない: そのときは戦う
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
console.log('目標を設定:', JSON.stringify(status))
const t0 = Date.now()
for (let step = 1; step <= maxSteps; step++) {
  const view = await call('GET', '/observe')
  if (view.busy) { await new Promise((resolve) => setTimeout(resolve, 1000)); step--; continue }
  const g = view.goal
  console.log(`\n[${step}] 残り=${g.remaining} 欲求=${view.needs.join('; ')}`)
  console.log('  ' + g.lines.join('\n  '))
  if (g.blocked.length) console.log('  進められない理由:', g.blocked.join('; '))
  if (g.met) { console.log(`目標を達成: ${step - 1} ステップ、${Math.round((Date.now() - t0) / 1000)}秒`); break }
  console.log(`  候補（${view.candidates.length}）: ${view.candidates.map((c) => c.id).join(' | ')}`)
  const c = choose(view)
  const r = await call('POST', '/act', { id: c.id })
  console.log(`  -> ${c.id}: ${r.ok ? '成功' : '失敗'} ${r.result}（${r.seconds}秒）`)
}

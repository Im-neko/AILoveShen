import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { optionsFor, ruleChoice, steerReflex, dangerView, movedToward } from '../src/reflex.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), breach: [] }

function bot (at, items = []) {
  return { entity: { position: at }, blockAt: () => ({ name: 'air', boundingBox: 'empty' }), inventory: { items: () => items.map(([name, count]) => ({ name, count })) } }
}

test('選択肢: 外なら戦う・逃げる・家に入る・距離を取る・続ける。クリーパーとは戦わない。家の中なら外に出ない', () => {
  const zombie = { name: 'zombie' }
  assert.deepEqual(optionsFor(bot(v(10.5, 70, 10.5)), { home }, zombie), ['fight', 'flee', 'go_home', 'keep_distance', 'ignore'])
  assert.deepEqual(optionsFor(bot(v(10.5, 70, 10.5)), { home }, { name: 'creeper' }), ['flee', 'keep_distance', 'ignore']) // 家へは連れて帰らない
  assert.deepEqual(optionsFor(bot(v(200.5, 70, 10.5)), { home }, zombie), ['fight', 'flee', 'keep_distance', 'ignore']) // 家が遠い
  assert.deepEqual(optionsFor(bot(v(2.5, 70, 2.5)), { home }, zombie), ['fight', 'ignore']) // 家の中
})

test('規則: 武器があり 1 体でクリーパーでなければ戦う、ほかは逃げる', () => {
  const opts = ['fight', 'flee', 'keep_distance', 'ignore']
  assert.equal(ruleChoice(bot(v(0, 70, 0), [['stone_sword', 1]]), { name: 'zombie' }, 1, opts), 'fight')
  assert.equal(ruleChoice(bot(v(0, 70, 0), [['stone_sword', 1]]), { name: 'zombie' }, 2, opts), 'flee')
  assert.equal(ruleChoice(bot(v(0, 70, 0)), { name: 'zombie' }, 1, opts), 'flee')
})

test('規則: 武器がなくても、体力が十分なら、ゾンビ・ドロウンド・クモ・スケルトンとは 1 体なら殴り合う。クリーパーは避ける', () => {
  const opts = ['fight', 'flee', 'keep_distance', 'ignore']
  const healthy = (health = 20, extra = {}) => ({ ...bot(v(0, 70, 0)), health, ...extra })
  for (const name of ['zombie', 'drowned', 'spider', 'skeleton']) assert.equal(ruleChoice(healthy(), { name }, 1, opts), 'fight', name)
  assert.equal(ruleChoice(healthy(), { name: 'creeper' }, 1, opts), 'flee')
  assert.equal(ruleChoice(healthy(), { name: 'zombie' }, 2, opts), 'flee', '2 体なら逃げる')
  assert.equal(ruleChoice(healthy(11), { name: 'zombie' }, 1, opts), 'flee', '体力 12 未満は逃げる')
  assert.equal(ruleChoice(healthy(20), { name: 'enderman' }, 1, opts), 'flee', '知らない相手は殴らない')
})

test('規則: 水の中では素手で戦わず、先に陸へ（flee）。武器があれば今までどおり', () => {
  const opts = ['fight', 'flee', 'keep_distance', 'ignore']
  const swimming = (items = []) => ({ ...bot(v(0, 62, 0), items), health: 20, entity: { position: v(0, 62, 0), isInWater: true } })
  assert.equal(ruleChoice(swimming(), { name: 'drowned' }, 1, opts), 'flee')
  assert.equal(ruleChoice(swimming([['stone_sword', 1]]), { name: 'drowned' }, 1, opts), 'fight')
})

test('Jev の選んだものに切り替える（選択肢にないもの、終わった反射は断る）', () => {
  const aborted = []
  const state = {
    danger: {
      id: 3, trigger: 'hurt', target: { name: 'skeleton' }, count: 1, options: ['fight', 'flee', 'ignore'],
      rule: 'flee', choice: 'flee', judged: null, next: null, started: Date.now(), health: 14,
      controller: { abort: (e) => aborted.push(e.message) }
    }
  }
  assert.deepEqual(steerReflex(state, { id: 3, choice: 'go_home' }), { accepted: false, why: 'go_home is not one of fight, flee, ignore' })
  assert.equal(steerReflex(state, { id: 2, choice: 'fight' }).accepted, false)
  assert.deepEqual(steerReflex(state, { id: 3, choice: 'fight', confidence: 0.82 }), { accepted: true })
  assert.equal(state.danger.next, 'fight')
  assert.deepEqual(aborted, ['switched to fight'])
  assert.deepEqual(dangerView(state).judged, { choice: 'fight', confidence: 0.82 })
  // 同じものなら止めない
  state.danger.choice = 'fight'
  steerReflex(state, { id: 3, choice: 'fight', confidence: 0.9 })
  assert.equal(aborted.length, 1)
})

test('向かってくる: モブ自身がこちらへ動いた距離（ボットが近づいた分は数えない）', () => {
  const me = v(0, 70, 0)
  assert.equal(movedToward(v(10, 70, 0), v(6, 70, 0), me), 4) // こちらへ 4 m
  assert.equal(movedToward(v(10, 70, 0), v(10, 70, 4), me), 0) // 横に動いた
  assert.equal(movedToward(v(10, 70, 0), v(10, 70, 0), me), 0) // 止まっている（ボットが近づいても 0）
})

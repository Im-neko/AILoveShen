import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { noteRefusal, refusalAt, listRefusals, isFutile, groupOf } from '../src/refusals.mjs'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const NOW = 1_000_000

test('同じことをしても同じ結果になる理由だけ載せる（一時的な理由・遠すぎる・持っていないは載せない）', () => {
  for (const why of ['failed: No path to the goal!', 'failed: Took to long to decide path to goal!', 'failed: could not walk to (1, 2, 3)',
    'refused: will not dig oak_log at 1,2,3: it is part of the current home', 'refused: there is air at 1,2,3, nothing to dig',
    'refused: the wheat at 1,2,3 is not ripe yet (age 1/7)', 'refused: 1,2,3 is taken by dirt']) assert.ok(isFutile(why), why)
  for (const why of ['refused: the reflex is handling a nearby threat', 'refused: another action is running', 'failed: interrupted: took damage',
    'refused: the block at 1,2,3 is 30m away (at most 5m)', 'refused: you have no dirt', 'failed: stopped by watch: x']) assert.ok(!isFutile(why), why)
})

test('1 回で載り、5 分で消える。動作の種類ごと（掘れなかった場所へ歩くのは妨げない）', () => {
  const state = {}
  assert.equal(noteRefusal(state, 'dig', v(1, 2, 3), 'refused: nothing to dig', NOW), true)
  assert.ok(refusalAt(state, 'dig', v(1, 2, 3), NOW + 1000))
  assert.equal(refusalAt(state, 'goto_pos', v(1, 2, 3), NOW + 1000), null)
  assert.equal(refusalAt(state, 'dig', v(1, 2, 4), NOW + 1000), null)
  assert.equal(refusalAt(state, 'dig', v(1, 2, 3), NOW + 5 * 60 * 1000 + 1), null)
  assert.equal(noteRefusal(state, 'dig', v(1, 2, 3), 'refused: the reflex is handling a nearby threat', NOW), false)
  assert.equal(groupOf('goto_memory'), groupOf('goto'))
})

test('/state に出す一覧は新しい順で、理由と何分前かがつく', () => {
  const state = {}
  noteRefusal(state, 'goto', v(5, 65, 5), 'failed: No path to the goal!', NOW - 3 * 60 * 1000)
  noteRefusal(state, 'dig', v(1, 2, 3), 'refused: will not dig oak_log at 1,2,3: it is part of a build (use build_next for it)', NOW)
  const list = listRefusals(state, NOW)
  assert.deepEqual(list.map((r) => [r.tool, r.x, r.minutes_ago]), [['dig', 1, 0], ['walk', 5, 3]])
  assert.match(list[0].reason, /^will not dig oak_log/)
})

test('候補は、断られた場所への同じ手を外し、理由を withheld に書く', () => {
  const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
  const blockAt = (p) => p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }
  const bot = { entity: { position: v(50.5, 70, 50.5) }, entities: {}, time: { timeOfDay: 1000 }, health: 20, food: 20, heldItem: null, blockAt, world: { raycast: () => null }, inventory: { items: () => [] } }
  const state = { home: null, plan: null, unreachableDrops: new Set() }
  const world = { dig: () => [v(60, 70, 60), v(70, 70, 70)], hunt: () => [] }
  const status = { leaves: [{ kind: 'dig', sources: ['oak_log'] }] }
  noteRefusal(state, 'dig', v(60, 70, 60), 'failed: No path to the goal!')
  const { candidates, withheld } = ground(bot, state, null, world, status)
  const digs = candidates.filter((c) => c.verb === 'dig')
  assert.ok(digs.every((c) => !(c.pos.x === 60)), JSON.stringify(digs.map((c) => c.id)))
  assert.match(withheld ?? '', /left out \(refused or unreachable a moment ago\).*No path/)
  void home
})

import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { solve } from '../src/solver.mjs'
import { knownFurnace } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const world = (inventory, blocks = {}, extra = {}) => ({
  inventory, blocks, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [], remembered: new Set(), furnace: null, ...extra
})
const torches = (w) => solve(k, w, [{ spec: 'torch', count: 4 }])
const kinds = (r) => r.leaves.map((l) => `${l.kind}:${l.item ?? ''}`)

test('かまどが近くにないが、家のかまどを覚えていれば、松明の石炭は探さず木炭を焼く', () => {
  // 遠いかまど: 新しく置くより歩くほうが安く、石炭を探し回る（100）より安い
  const away = torches(world({ oak_log: 2, oak_planks: 4, stick: 4 }, {}, { furnaceAway: true }))
  assert.match(away.lines.join('\n'), /1 charcoal.*smelt log in a furnace/s)
  assert.deepEqual(kinds(away), ['smelt:charcoal'])
  // かまどを覚えていなければ、今までどおり石炭を探す（かまどを新しく置くほうが高い）
  const none = torches(world({ stick: 4, wooden_pickaxe: 1, oak_log: 2 }, {}))
  assert.match(none.lines.join('\n'), /coal/)
  assert.equal(none.leaves[0].kind, 'explore')
  // 石炭が見えていれば石炭のまま（安い）
  const seen = torches(world({ stick: 4, wooden_pickaxe: 1 }, { coal_ore: 3 }, { furnaceAway: true }))
  assert.deepEqual(kinds(seen), ['dig:coal'])
})

test('焼けるかまどは、近くのもの、なければ最後に見たかまど、家のかまど。近くのを見たら覚える', () => {
  const near = { position: new Vec3(3, 64, 3) }
  const bot = (found) => ({ registry: md, entity: { position: new Vec3(0, 64, 0) }, findBlock: () => found, blockAt: () => ({ name: 'air' }) })
  const state = {}
  assert.deepEqual(knownFurnace(bot(near), state), { position: near.position, near: true })
  assert.deepEqual(state.lastFurnace, { x: 3, y: 64, z: 3 })
  const far = knownFurnace(bot(null), state)
  assert.equal(far.near, false)
  assert.deepEqual(far.position, new Vec3(3, 64, 3))
  assert.equal(knownFurnace(bot(null), {}), null) // 覚えていなければ null
  assert.equal(knownFurnace(bot(null)), null) // state がなければ遠くは探さない
})

test('地下の鉱石が見えず採掘場もなければ、探索の候補は残したまま、採掘場を作るよう促す', () => {
  const r = solve(k, world({ wooden_pickaxe: 1, torch: 8, wooden_sword: 1 }, {}, { mines: [] }), [{ spec: 'coal', count: 2 }])
  assert.equal(r.leaves[0].kind, 'explore') // 探索は残す
  assert.match(r.blocked.join('\n'), /instead of walking around for it, add a mine: facility\(<new name>/)
  // 作りかけの採掘場があれば、それを続けるよう言う
  const building = solve(k, world({ wooden_pickaxe: 1, torch: 8, wooden_sword: 1 }, {}, { mines: [{ name: 'mine_1', status: 'building' }] }), [{ spec: 'coal', count: 2 }])
  assert.match(building.blocked.join('\n'), /the mine mine_1 \(building\) is not finished: finish it/)
  // 完成した採掘場があれば、そこから下りる（促さない）
  const done = solve(k, world({ wooden_pickaxe: 1, torch: 8, wooden_sword: 1 }, {}, { mine: { name: 'mine_1', bottom: { x: 0, y: 30, z: 0 }, above: true }, mines: [{ name: 'mine_1', status: 'done' }] }), [{ spec: 'coal', count: 2 }])
  assert.equal(done.leaves[0].kind, 'go_mine')
  assert.doesNotMatch(done.blocked.join('\n'), /add a mine/)
  // 地下でない物（原木）には促さない
  const log = solve(k, world({}, {}, { mines: [] }), [{ spec: 'oak_log', count: 2 }])
  assert.doesNotMatch(log.blocked.join('\n'), /add a mine/)
})

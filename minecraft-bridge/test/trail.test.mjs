import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { trailSpot, placeTrailTorch, startTrail, torchesNeeded, TRAIL_VERBS, TORCH_CAP, TORCH_MIN_GAP } from '../src/trail.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)

function bot ({ torches = 5, held = { name: 'stone_pickaxe', count: 1 }, onGround = true, floor = 'stone', digging = false } = {}) {
  const placed = new Map()
  const items = torches ? [{ name: 'torch', count: torches }, held].filter(Boolean) : [held].filter(Boolean)
  const b = {
    entity: { position: v(10.5, 30, 10.5), onGround },
    heldItem: held,
    inventory: { items: () => items },
    pathfinder: { isMining: () => false, isBuilding: () => false },
    targetDigBlock: digging ? {} : null,
    blockAt: (p) => {
      const key = `${p.x},${p.y},${p.z}`
      if (placed.has(key)) return { name: placed.get(key), boundingBox: 'empty' }
      if (p.y < 30) return { name: floor, boundingBox: 'block' }
      return { name: 'air', boundingBox: 'empty' }
    },
    equips: [],
    equip: async (item) => { b.equips.push(item.name); b.heldItem = item },
    placeBlock: async (ref, face) => { placed.set(`${ref.position.x + face.x},${ref.position.y + face.y},${ref.position.z + face.z}`, 'torch') }
  }
  // blockAt が返すブロックに位置を持たせる
  const at = b.blockAt
  b.blockAt = (p) => { const x = at(p); return x && { ...x, position: p } }
  return b
}
const dark = () => true
const bright = () => false

test('暗くて松明を持っていれば、足元に置く。置いたあとは持っていた物を持ち直す', async () => {
  const b = bot()
  const spot = await placeTrailTorch(b, {}, dark)
  assert.deepEqual([spot.x, spot.y, spot.z], [10, 30, 10])
  assert.equal(b.blockAt(spot).name, 'torch')
  assert.deepEqual(b.equips, ['torch', 'stone_pickaxe'])
})

test('持ち替えを待つ間に行動が道具を持ち直したら、鶴嘴を置こうとせず、持っていた物を持ち直す', async () => {
  const b = bot()
  const equip = b.equip
  b.equip = async (item) => { await equip(item); b.heldItem = { name: 'stone_pickaxe', count: 1 } } // 掘る手が持ち直した
  let placed = false
  const placeBlock = b.placeBlock
  b.placeBlock = async (...args) => { placed = true; return placeBlock(...args) }
  await assert.rejects(placeTrailTorch(b, {}, dark), (e) => e.swapped === true && /held item changed/.test(e.message))
  assert.equal(placed, false)
})

test('置かないとき: 明るい、松明がない、空中、掘っている、反射が動いている、家の中', () => {
  assert.equal(trailSpot(bot(), {}, bright), null)
  assert.equal(trailSpot(bot({ torches: 0 }), {}, dark), null)
  assert.equal(trailSpot(bot({ onGround: false }), {}, dark), null)
  assert.equal(trailSpot(bot({ digging: true }), {}, dark), null)
  assert.equal(trailSpot(bot(), { reflex: true }, dark), null)
  assert.ok(trailSpot(bot(), {}, dark))
})

test('暗い所へ入る前に持つ松明の数は、行き先までの距離から（約 9 m に 1 本と、予備 4 本。上限 32）', () => {
  assert.equal(torchesNeeded(9), 5)
  assert.equal(torchesNeeded(111), 17)
  assert.equal(torchesNeeded(1000), TORCH_CAP)
})

test('歩く・掘る行動だけで置く（戦う・逃げる・寝るなどは置かない。dig_mine は自分で置く）', () => {
  for (const verb of ['goto_pos', 'dig', 'dig_down', 'explore', 'go_home']) assert.ok(TRAIL_VERBS.has(verb), verb)
  for (const verb of ['leave_burrow', 'climb_out', 'dig_out', 'surface']) assert.ok(TRAIL_VERBS.has(verb), verb) // 竪穴を登る間も照らす
  for (const verb of ['attack', 'flee', 'sleep', 'dig_mine', 'craft', 'eat']) assert.ok(!TRAIL_VERBS.has(verb), verb)
})

test('置いた所から TORCH_MIN_GAP 以内では置き直さない（竪穴を登ると足元の松明は壊されるので、暗いまま使い切らないように）', async () => {
  // 床は高さ 30 まで、その上は空気。足元の松明が壊されたあとの暗い竪穴を、上へ動かして見る
  const b = bot()
  const placed = []
  const base = b.blockAt
  b.blockAt = (p) => (p.y >= 30 && p.y < b.entity.position.y - 0.5 && !placed.some((q) => q.equals(p)) ? { name: 'cobblestone', boundingBox: 'block', position: p } : base(p))
  b.placeBlock = async (ref, face) => { placed.push(ref.position.plus(face)) }
  b.blockAt = ((at) => (p) => (placed.some((q) => q.equals(p)) ? { name: 'torch', boundingBox: 'empty', position: p } : at(p)))(b.blockAt)
  const logs = []
  const count = () => logs.filter((m) => m.includes('置いた')).length
  const stop = startTrail(b, {}, (m) => logs.push(m), dark)
  try {
    await new Promise((r) => setTimeout(r, 1200))
    assert.equal(count(), 1, `最初の 1 本だけ: ${logs.join(' / ')}`)
    placed.length = 0 // 足場を置くときに壊された
    b.entity.position = v(10.5, 30 + TORCH_MIN_GAP - 2, 10.5)
    await new Promise((r) => setTimeout(r, 1200))
    assert.equal(count(), 1, `${TORCH_MIN_GAP} m 以内では置き直さない: ${logs.join(' / ')}`)
    b.entity.position = v(10.5, 30 + TORCH_MIN_GAP + 2, 10.5)
    await new Promise((r) => setTimeout(r, 1200))
    assert.equal(count(), 2, `離れたらまた置く: ${logs.join(' / ')}`)
  } finally {
    stop()
  }
})

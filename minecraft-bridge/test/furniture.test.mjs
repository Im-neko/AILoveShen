import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { placedToTake } from '../src/furniture.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const home = { door: new Vec3(2, 70, 4), inside: new Vec3(2, 70, 3), min: new Vec3(1, 70, 1), max: new Vec3(3, 70, 3) }

function bot (beds) {
  return {
    registry: md,
    entity: { position: new Vec3(10, 70, 10) },
    findBlocks: () => beds,
    blockAt: () => ({ name: 'red_bed' })
  }
}

test('家に運べるベッドは、今の家の外に置いてある一番近いもの', () => {
  const inHome = new Vec3(1, 70, 1)
  const far = new Vec3(30, 70, 30)
  const near = new Vec3(12, 70, 10)
  assert.deepEqual(placedToTake(bot([inHome, far, near]), { home }, 'bed').pos, near)
  assert.equal(placedToTake(bot([inHome]), { home }, 'bed'), null)
  assert.deepEqual(placedToTake(bot([inHome]), { home: null }, 'bed').pos, inHome) // 家がなければどれでも
})

test('家の中の作業台・かまど・チェストを見つける（床とその上の段）', async () => {
  const { furnitureInHome } = await import('../src/furniture.mjs')
  const placed = new Map([['3,70,1', 'crafting_table'], ['1,71,3', 'furnace']])
  const b = { blockAt: (p) => ({ name: placed.get(`${p.x},${p.y},${p.z}`) ?? 'air' }) }
  assert.deepEqual(furnitureInHome(b, home, 'crafting_table'), new Vec3(3, 70, 1))
  assert.deepEqual(furnitureInHome(b, home, 'furnace'), new Vec3(1, 71, 3))
  assert.equal(furnitureInHome(b, home, 'chest'), null)
  assert.equal(furnitureInHome(b, { ...home, cells: [{ x: 1, z: 1 }] }, 'crafting_table'), null) // 部屋のセルだけ見る
})

test('断られたベッドは拾えるものに数えない（作るほうへ回る）', () => {
  const near = new Vec3(12, 70, 10)
  const refusals = new Map([['dig:12,70,10', { until: Date.now() + 60000, reason: 'No path' }]])
  assert.equal(placedToTake(bot([near]), { home, refusals }, 'bed'), null)
})

test('ベッドが近くに置いてあれば、have(bed) は作らず拾う手を出す', async () => {
  const { checkConditions } = await import('../src/goals.mjs')
  const { Knowledge } = await import('../src/knowledge.mjs')
  const k = new Knowledge(md)
  const near = new Vec3(12, 70, 10)
  const b = { ...bot([near]), time: { timeOfDay: 1000 }, inventory: { items: () => [] } }
  const world = { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }
  const state = { home, plan: null, goal: { spec: { predicate: 'at_home' } } }
  const [r] = checkConditions([{ predicate: 'have', item: 'bed', count: 1 }], b, state, k, world)
  assert.equal(r.met, false)
  assert.match(r.lines.join('\n'), /a placed red_bed at 12,70,10 can be taken instead of made/)
})

test('作業台・かまど・チェストも、家の外に置いてあれば have で作らず拾う。中身のあるチェストと別の種類は拾わない', async () => {
  const at = (name, p) => ({ ...bot([p]), blockAt: () => ({ name }) })
  const near = new Vec3(12, 70, 10)
  assert.deepEqual(placedToTake(at('crafting_table', near), { home }, 'crafting_table'), { pos: near, block: 'crafting_table' })
  assert.equal(placedToTake(at('blast_furnace', near), { home }, 'furnace'), null) // 溶鉱炉は furnace ではない
  assert.equal(placedToTake(at('crafting_table', near), { home }, 'torch'), null)
  const chests = { home, memory: { chests: { '12,70,10': { contents: { oak_log: 3 } } } } }
  assert.equal(placedToTake(at('chest', near), chests, 'chest'), null) // 先に中身を出す
  // チェストは自分で置いた物だけ（村のチェストは中身が分からず、壊すと散らばる）
  assert.equal(placedToTake(at('chest', near), { home }, 'chest'), null)
  const mine = { home, placed: { '12,70,10': { name: 'chest', at: 0 } } }
  assert.deepEqual(placedToTake(at('chest', near), mine, 'chest'), { pos: near, block: 'chest' })
})

test('チェストをもう 1 つ家に置くとき、家の外に自分で置いたチェストがあれば、作らずに拾って運ぶ', async () => {
  const { makeGoal, evaluate } = await import('../src/goals.mjs')
  const { Knowledge } = await import('../src/knowledge.mjs')
  const k = new Knowledge(md)
  const outside = new Vec3(12, 70, 10)
  const homeChest = new Vec3(1, 70, 1)
  const b = {
    ...bot([outside]),
    time: { timeOfDay: 1000 },
    inventory: { items: () => [] },
    blockAt: (p) => (p.equals(outside) || p.equals(homeChest) ? { name: 'chest', position: p } : { name: p.y < 70 ? 'stone' : 'air', boundingBox: p.y < 70 ? 'block' : 'empty', position: p })
  }
  const world = { inventory: {}, stored: {}, smelting: {}, blocks: {}, mobs: {}, table: 'near', furnace: null, unlocked: () => true, remembered: new Set(), dig: () => [], hunt: () => [] }
  const run = (placed, where = 'home') => {
    const state = { home, plan: null, placed, builds: {}, formerHomes: [] }
    const goal = makeGoal({ predicate: 'placed', item: 'chest', where, count: 2 }, b, state, k)
    return evaluate(b, { ...state, goal }, k, world)
  }
  const mine = run({ '12,70,10': { name: 'chest', at: 0 } })
  assert.equal(mine.met, false)
  assert.deepEqual(mine.leaves.map((l) => l.kind), ['take_placed'])
  assert.deepEqual([mine.leaves[0].pos, mine.leaves[0].block], [outside, 'chest'])
  // 家の中でなく、ほかの場所に置く頼みなら、拾って運ばない
  assert.ok(!run({ '12,70,10': { name: 'chest', at: 0 } }, 'near_home').leaves.some((l) => l.kind === 'take_placed'))
  // 自分で置いたのでない外のチェスト（村の物）は拾わず、作る
  const theirs = run({})
  assert.ok(!theirs.leaves.some((l) => l.kind === 'take_placed'), JSON.stringify(theirs.leaves))
})

test('建物の一部の作業台は、拾える家具に数えない', async () => {
  const near = new Vec3(12, 70, 10)
  const b = { ...bot([near]), blockAt: () => ({ name: 'crafting_table' }) }
  const state = { home, plan: { origin: new Vec3(10, 70, 8), size: { width: 6, depth: 6, height: 4 } } }
  assert.equal(placedToTake(b, state, 'crafting_table'), null)
})

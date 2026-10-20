// 街の道を敷く（設計書 167）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { setRoads, roadColumns, columnKind, roadStatus, paveCandidate } from '../src/roads.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')
const signal = new AbortController().signal

// 地面は y 63 が草（その下は土）。道の高さの目安は 63（家の床の下）。blocks で上書き
function world (blocks = {}) {
  const set = new Map(Object.entries(blocks))
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const name = set.get(`${q.x},${q.y},${q.z}`) ?? (q.y < 63 ? 'dirt' : q.y === 63 ? 'grass_block' : 'air')
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function fakeBot (w, { shovel = true } = {}) {
  const inv = shovel ? [{ name: 'stone_shovel', count: 1, type: md.itemsByName.stone_shovel.id }] : []
  return {
    registry: md,
    entity: { position: new Vec3(0.5, 64, 0.5), onGround: true, effects: {} },
    entities: {},
    time: { age: 1000, timeOfDay: 1000 },
    blockAt: w.blockAt,
    world: { getBlock: w.blockAt },
    inventory: { items: () => inv },
    heldItem: null,
    equip: async () => {},
    lookAt: async () => {},
    waitForTicks: async () => {},
    dig: async (b) => { w.set.set(`${b.position.x},${b.position.y},${b.position.z}`, 'air') },
    activateBlock: async (b) => { if (/^(grass_block|dirt)$/.test(b.name)) w.set.set(`${b.position.x},${b.position.y},${b.position.z}`, 'dirt_path') }
  }
}

const home = () => ({ min: new Vec3(20, 64, 20), max: new Vec3(22, 64, 22), inside: new Vec3(21, 64, 21), outside: new Vec3(21, 64, 25), door: new Vec3(21, 64, 24), bed: null })
const newState = (extra = {}) => ({ home: null, formerHomes: [], builds: {}, plan: null, facilities: {}, ...extra })

test('setRoads: 列の四角を受け取る。壊れた列は断る', () => {
  const state = newState()
  assert.equal(setRoads(state, [{ min_x: 0, min_z: 0, max_x: 2, max_z: 2 }]), 1)
  assert.throws(() => setRoads(state, [{ min_x: 3, min_z: 0, max_x: 2, max_z: 2 }]), /min_x <= max_x/)
  assert.throws(() => setRoads(state, 'x'), /list/)
})

test('roadColumns: 四角のマスの重なりは 1 回。戸口へ、一番近い列から 1 マス幅でつなぐ', () => {
  const state = newState({ home: home() })
  setRoads(state, [{ min_x: 19, min_z: 29, max_x: 23, max_z: 31 }, { min_x: 21, min_z: 28, max_x: 23, max_z: 32 }])
  const cols = roadColumns(state)
  assert.equal(new Set(cols.map((c) => `${c.x},${c.z}`)).size, cols.length)
  assert.ok(cols.some((c) => c.x === 21 && c.z === 26), '戸口（21,25）まで北へつなぐ')
  assert.ok(cols.some((c) => c.x === 21 && c.z === 25))
})

test('columnKind: 草は敷く、土の道と石は済み、水・木・家・畑は敷かない', () => {
  const state = newState({ home: home(), facilities: { farm_a: { name: 'farm_a', kind: 'farm', box: { min: { x: 40, y: 63, z: 40 }, max: { x: 44, y: 63, z: 44 } } } } })
  const w = world({ '1,63,0': 'dirt_path', '2,63,0': 'stone', '3,63,0': 'water', '4,63,0': 'oak_log', '5,64,0': 'short_grass', '6,64,0': 'stone', '7,64,0': 'dandelion', '9,65,0': 'oak_leaves' })
  const bot = fakeBot(w)
  const kind = (x, z = 0) => columnKind(bot, state, x, z, 63).kind
  assert.equal(kind(0), 'todo')
  assert.equal(kind(1), 'done')
  assert.equal(kind(2), 'done')
  assert.equal(kind(3), 'unfit')
  assert.equal(kind(4), 'unfit')
  assert.equal(kind(5), 'todo', '草が上にあっても敷ける（先に刈る）')
  assert.equal(kind(6), 'done', '一段高い石の面は、地面そのもの（済み）')
  assert.equal(kind(9), 'unfit', '木の葉が上にある')
  assert.equal(kind(21, 21), 'kept', '家の上')
  assert.equal(kind(42, 42), 'kept', '畑の上')
})

test('paveCandidate: シャベルがあり、敷く所が近いときだけ出る', () => {
  const state = newState({ home: home() })
  setRoads(state, [{ min_x: 0, min_z: 0, max_x: 2, max_z: 2 }])
  const dist = (bot, p) => Math.round(bot.entity.position.distanceTo(p))
  const w = world()
  assert.equal(paveCandidate(fakeBot(w), state, dist).length, 1)
  assert.match(paveCandidate(fakeBot(w), state, dist)[0].id, /\(9 of 9 cells left\)/)
  assert.deepEqual(paveCandidate(fakeBot(w, { shovel: false }), state, dist), [])
  const far = newState({ home: home() })
  setRoads(far, [{ min_x: 300, min_z: 300, max_x: 302, max_z: 302 }])
  assert.deepEqual(paveCandidate(fakeBot(w), far, dist), [])
  assert.deepEqual(paveCandidate(fakeBot(w), newState(), dist), [])
})

test('pave: 近い順に全部敷く。草を刈ってから。済んだ所は残りに数えない。シャベルがなければ断る', async () => {
  const state = newState({ home: home() })
  setRoads(state, [{ min_x: 0, min_z: 0, max_x: 2, max_z: 1 }])
  const w = world({ '1,64,0': 'short_grass', '2,63,1': 'stone' })
  const bot = fakeBot(w)
  const r = await PRIMITIVES.pave(bot, state, {}, signal)
  assert.match(r, /laid 5 cells of dirt path; the planned roads are all laid/)
  for (const [x, z] of [[0, 0], [1, 0], [2, 0], [0, 1], [1, 1]]) assert.equal(w.blockAt(new Vec3(x, 63, z)).name, 'dirt_path', `${x},${z}`)
  assert.equal(w.blockAt(new Vec3(1, 64, 0)).name, 'air', '草は刈った')
  assert.equal(w.blockAt(new Vec3(2, 63, 1)).name, 'stone', '石の上は済み')
  assert.equal(roadStatus(bot, state, { fresh: true }).todo.length, 0)
  await assert.rejects(PRIMITIVES.pave(bot, state, {}, signal), /could not lay any path/)
  const bare = fakeBot(world(), { shovel: false })
  await assert.rejects(PRIMITIVES.pave(bare, newState({ home: home(), roads: state.roads }), {}, signal), /the shovel is gone/)
})

test('paved: 計画した道がすべて土の道になったら達成。道がなければ目標にできない。シャベルがなければ作るところから（設計書 200）', async () => {
  const { Knowledge } = await import('../src/knowledge.mjs')
  const { checkConditions, makeGoal } = await import('../src/goals.mjs')
  const k = new Knowledge(md)
  const world0 = { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, dig: () => [], hunt: () => [] }
  const state = newState({ home: home() })
  const w = world({ '1,64,0': 'short_grass' })
  const bot = fakeBot(w, { shovel: false })

  assert.throws(() => makeGoal({ predicate: 'paved' }, bot, state, k), /no planned roads/)
  const [none] = checkConditions([{ predicate: 'paved' }], bot, state, k, world0)
  assert.equal(none.met, false)

  setRoads(state, [{ min_x: 0, min_z: 0, max_x: 2, max_z: 1 }])
  const [bare] = checkConditions([{ predicate: 'paved' }], bot, state, k, world0)
  assert.equal(bare.met, false)
  assert.match(bare.lines.join('\n'), /planned road cells: 0 of 6 laid/)

  const laid = fakeBot(w)
  await PRIMITIVES.pave(laid, state, {}, signal)
  const [done] = checkConditions([{ predicate: 'paved' }], laid, state, k, world0)
  assert.equal(done.met, true)
})

import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate, checkConditions } from '../src/goals.mjs'
import { ground } from '../src/candidates.mjs'
import { newMemory, rememberChest, rememberSite, storedCounts } from '../src/memory.mjs'
import { plannedSites, ensureSurvey, surveySite, SURVEY_RADIUS } from '../src/survey.mjs'
import { settleHome, inHouse } from '../src/home.mjs'
import { BuildPlan } from '../src/build.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const AIR = { name: 'air', boundingBox: 'empty' }
const world = ({ inventory = {}, stored = {} } = {}) =>
  ({ inventory, stored, blocks: {}, mobs: {}, table: 'near', unlocked: () => true, dig: () => [], hunt: () => [] })

// y 70 に 3x3 の室内（x 1..3、z 1..3）、ドアは (2, 70, 4)
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const flat = (p) => (p.y < 70 ? { name: 'grass_block', boundingBox: 'block' } : AIR)
const bot = (at = v(2, 70, 2), blockAt = flat) => ({
  entity: { position: at },
  entities: {},
  time: { timeOfDay: 1000, age: 100 },
  health: 20,
  food: 20,
  heldItem: null,
  registry: md,
  inventory: { items: () => [], emptySlotCount: () => 20 },
  blockAt,
  findBlocks: () => []
})

test('stored() は家のチェストだけを数え、前の家のチェストは取り出す元になる', () => {
  const memory = newMemory()
  rememberChest(memory, v(2, 70, 1), { bread: 2 }, 0) // 家の中
  rememberChest(memory, v(200, 70, 0), { bread: 10 }, 0) // 前の家
  assert.deepEqual(storedCounts(memory, home), { bread: 2 })
  assert.deepEqual(storedCounts(memory), { bread: 12 })

  const b = bot()
  const [r] = checkConditions([{ predicate: 'stored', item: 'food', count: 8 }], b, { home, plan: null, memory }, k, world())
  assert.equal(r.met, false)

  // 蓄える目標では、家のチェストからは取り出さず、前の家のチェストから運ぶ
  const state = { home, plan: null, memory, unreachableDrops: new Set() }
  state.goal = makeGoal({ predicate: 'stored', item: 'food', count: 8 }, b, state, k)
  const status = evaluate(b, state, k, world({ stored: storedCounts(memory) }))
  const { candidates } = ground(b, state, k, world({ stored: storedCounts(memory) }), status)
  const takes = candidates.filter((c) => c.verb === 'withdraw').map((c) => c.id)
  assert.deepEqual(takes, ['take 6 bread from the chest at 200,70,0'], candidates.map((c) => c.id).join('\n'))
})

test('候補地は中心と 8 方向の 9 か所で、調査の計画は 1 回だけ作る', () => {
  const sites = plannedSites({ x: 0.5, z: 0.5 })
  assert.equal(sites.length, 9)
  assert.deepEqual(sites[0], { id: 'here', x: 0, z: 0 })
  assert.deepEqual(sites.find((s) => s.id === 'E'), { id: 'E', x: SURVEY_RADIUS, z: 0 })
  const state = { home }
  ensureSurvey(state, bot(v(50, 70, 50)))
  assert.deepEqual(state.survey.center, { x: 2, z: 3 }) // 家があれば家が中心
  const first = state.survey
  ensureSurvey(state, bot(v(500, 70, 500)))
  assert.equal(state.survey, first)
})

test('surveyed(n): 家ができるまでは判定を待ち、候補地の中心を決めない', () => {
  const state = { home: null, plan: null, memory: newMemory() }
  const [r] = checkConditions([{ predicate: 'surveyed', count: 9 }], bot(v(40, 70, 40)), state, k, world())
  assert.equal(r.met, false)
  assert.equal(state.survey, undefined)
})

test('surveyed(n): 一番近い未調査の候補地を調べる候補を出し、調べた数で判定する', () => {
  const b = bot()
  const state = { home, plan: null, memory: newMemory(), unreachableDrops: new Set() }
  assert.throws(() => makeGoal({ predicate: 'surveyed', count: 10 }, b, state, k), /count must be 1-9/)
  state.goal = makeGoal({ predicate: 'surveyed', count: 2 }, b, state, k)

  const first = evaluate(b, state, k, world())
  assert.equal(first.met, false)
  assert.equal(first.lines[0], 'candidate sites surveyed 0/2')
  const { candidates } = ground(b, state, k, world(), first)
  assert.ok(candidates.some((c) => c.id === 'survey the here site at 2,3'), candidates.map((c) => c.id).join('\n'))

  rememberSite(state.memory, { id: 'here', x: 2, z: 3 }, 100)
  const second = evaluate(b, state, k, world())
  assert.ok(second.remaining < first.remaining, `${second.remaining} < ${first.remaining}`)
  assert.equal(second.leaves[0].kind, 'survey')
  assert.notEqual(second.leaves[0].site.id, 'here')

  rememberSite(state.memory, { id: second.leaves[0].site.id }, 200)
  assert.equal(evaluate(b, state, k, world()).met, true)
  const [check] = checkConditions([{ predicate: 'surveyed', count: 2 }], b, state, k, world())
  assert.equal(check.met, true)
})

test('候補地の数字: 平らな区画、水、急な所', () => {
  const site = { id: 'here', x: 0, z: 0 }
  const plain = surveySite(bot(v(0, 70, 0)), { survey: { center: { x: 0, z: 0 } } }, site)
  assert.ok(plain.flat_plots >= 20, `${plain.flat_plots}`)
  assert.equal(plain.water_pct, 0)
  assert.equal(plain.steep_pct, 0)
  assert.equal(plain.distance, 0)

  // 西半分は海、東の端は崖（x >= 20 で 6 段高い）
  const coast = (p) => {
    const top = p.x >= 20 ? 76 : 70
    if (p.x < 0) return p.y < 69 ? { name: 'sand', boundingBox: 'block' } : p.y === 69 ? { name: 'water', boundingBox: 'block' } : AIR
    return p.y < top ? { name: 'grass_block', boundingBox: 'block' } : AIR
  }
  const r = surveySite(bot(v(0, 70, 0), coast), { survey: { center: { x: 0, z: 0 } } }, site)
  assert.ok(r.water_pct >= 45 && r.water_pct <= 55, `${r.water_pct}`)
  assert.ok(r.steep_pct > 0)
  assert.ok(r.flat_plots < plain.flat_plots)
})

test('別の場所に建て終わったら引っ越し、前の家は残して守る', () => {
  const blocks = []
  for (let x = 0; x < 3; x++) for (let z = 0; z < 3; z++) if (x !== 1 || z !== 1) blocks.push({ x, y: 0, z, block: x === 1 && z === 0 ? 'door' : 'planks' })
  const plan = new BuildPlan({ blocks, width: 3, depth: 3, height: 1, site: { x: 100.7, z: 0 } })
  assert.deepEqual(plan.site, { x: 100, z: 0 })
  assert.deepEqual(BuildPlan.fromJSON(JSON.parse(JSON.stringify(plan.toJSON()))).site, { x: 100, z: 0 })

  const state = { home, plan, formerHomes: [] }
  const placedAll = (p) => (p.x >= 100 && p.x <= 102 && p.z >= 0 && p.z <= 2 && p.y === 70
    ? { name: p.x === 101 && p.z === 0 ? 'oak_door' : 'oak_planks', boundingBox: 'block', getProperties: () => ({ facing: 'north' }) }
    : flat(p))
  // 建てている間は今の家のまま
  assert.equal(settleHome(state, bot(v(0, 70, 0))), false)
  plan.origin = v(100, 70, 0)
  assert.equal(settleHome(state, bot(v(0, 70, 0))), false)
  assert.equal(state.home, home)

  assert.equal(settleHome(state, bot(v(0, 70, 0), placedAll)), true)
  assert.deepEqual(state.home.door, v(101, 70, 0))
  assert.deepEqual(state.formerHomes, [home])
  assert.equal(settleHome(state, bot(v(0, 70, 0), placedAll)), false) // 同じ家では何も変わらない
  assert.ok(inHouse(state, v(2, 70, 2))) // 前の家は掘らない
})

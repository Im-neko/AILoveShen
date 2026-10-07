import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { exitSpots } from '../src/home.mjs'
import minecraftData from 'minecraft-data'
import { ground } from '../src/candidates.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const knowledge = new Knowledge(minecraftData('1.21.4'))
const DAY = 1000
const NIGHT = 18000

// y 70 の石の上の 5x5 の家: 壁は x 0..4、z 0..4（板材、高さ 2）、室内は 1..3、ドアは (2, 70, 4)
function world ({ blocked = [], sky } = {}) {
  const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
  const blockAt = (p) => {
    const key = `${p.x},${p.y},${p.z}`
    if (p.y < 70) return { name: 'stone', boundingBox: 'block' }
    if (blocked.includes(key)) return { name: 'stone', boundingBox: 'block' }
    if (p.x === 2 && p.z === 4 && p.y <= 71) return { name: 'oak_door', boundingBox: 'block', getProperties: () => ({ open: false }) }
    const wall = (p.x === 0 || p.x === 4 || p.z === 0 || p.z === 4) && p.x >= 0 && p.x <= 4 && p.z >= 0 && p.z <= 4
    if (wall && p.y <= 71 && !(p.x === 2 && p.z === 4)) return { name: 'oak_planks', boundingBox: 'block' }
    return { name: 'air', boundingBox: 'empty', skyLight: sky }
  }
  return { home, blockAt }
}

function fakeBot ({ time, at, mobs = [], blockAt, health = 20, items = [] }) {
  const entity = { position: at, eyeHeight: 1.62 }
  const entities = { 0: entity }
  mobs.forEach((m, i) => { entities[i + 1] = { id: i + 1, type: 'hostile', height: 1.99, ...m } })
  return {
    entity,
    entities,
    time: { timeOfDay: time },
    health,
    food: 20,
    heldItem: null,
    blockAt,
    world: { raycast: () => ({ name: 'oak_planks' }) }, // 壁で外は何も見えない
    inventory: { items: () => items.map(([name, count]) => ({ name, count })) }
  }
}

const skeletonAtDoor = { name: 'skeleton', position: v(2.5, 70, 6.5) }
const digOutside = { leaves: [{ kind: 'dig', sources: ['oak_log'] }] }
const snap = { dig: () => [v(10, 70, 10)], hunt: () => [] }

function run ({ time = DAY, mobs = [skeletonAtDoor], status = digOutside, blocked, sky, health, bed = false, items = [] } = {}) {
  const { home, blockAt: base } = world({ blocked, sky })
  const bedAt = v(1, 70, 1)
  if (bed) home.bed = bedAt
  const blockAt = (p) => bed && p.equals(bedAt) ? { name: 'red_bed', boundingBox: 'block' } : base(p)
  const bot = fakeBot({ time, at: v(2.5, 70, 2.5), mobs, blockAt, health, items })
  const state = { home, plan: null, unreachableDrops: new Set() }
  return ground(bot, state, knowledge, snap, status)
}

test('出口は空いている各面に出し、敵対モブから遠い順に並べる', () => {
  const { home, blockAt } = world()
  const spots = exitSpots({ blockAt }, home, [skeletonAtDoor])
  assert.deepEqual(spots.map((s) => s.side), ['north', 'west', 'east', 'south'])
  assert.deepEqual(spots[0].wall, v(1, 70, 0)) // 角に寄ったマスのほうが少し遠い
  assert.deepEqual(spots[0].step, v(1, 70, -1))
  // ドアそのものは出口にしない
  assert.ok(spots.every((s) => !(s.wall.x === 2 && s.wall.z === 4)))
})

test('外に立つ場所がない面には出口を出さない', () => {
  const blocked = [1, 2, 3].map((x) => `${x},70,-1`)
  const { home, blockAt } = world({ blocked })
  const spots = exitSpots({ blockAt }, home, [skeletonAtDoor])
  assert.ok(!spots.some((s) => s.side === 'north'))
})

// 値打ちのある持ち物（ダイヤのツルハシ）を持って体力が低い: 外に出る危なさが極めて高い（risk.mjs）
const VALUABLE = [['diamond_pickaxe', 1]]

test('昼にドアの前に敵対モブがいても、危なさが低ければ外での作業を控えない。敵を避ける出口も候補に出す', () => {
  const { candidates, withheld } = run()
  const verbs = candidates.map((c) => c.verb)
  assert.ok(verbs.includes('dig'))
  assert.ok(verbs.includes('exit_wall'))
  assert.equal(withheld, null)
})

test('昼にドアの前に敵対モブがいて、危なさが極めて高ければ、外での作業を控え、出口を候補に出す', () => {
  const { candidates, withheld } = run({ health: 4, items: VALUABLE })
  const verbs = candidates.map((c) => c.verb)
  assert.ok(!verbs.includes('dig'))
  assert.ok(verbs.includes('exit_wall'))
  assert.match(withheld, /staying inside: 1 actions outside are held back \(risk extreme \(health 4\/20/)
})

test('cleared の目標なら、ドアの前で待つものと戦いに出てよい（素手でも）', () => {
  const mob = { ...skeletonAtDoor, id: 1 }
  const { candidates } = run({ status: { leaves: [{ kind: 'clear', mobs: [mob] }] } })
  const attack = candidates.find((c) => c.verb === 'attack')
  assert.ok(attack)
  assert.equal(attack.weapon, 'none (fist)')
})

test('待つのは、時間がたてば変わることがあるときだけ候補に出す', () => {
  const husk = { name: 'husk', position: v(2.5, 70, 6.5) }
  const ids = (opts) => run(opts).candidates.filter((c) => c.verb === 'wait').map((c) => c.id)
  // 体力が満タンで、ハスクが日陰にいても日なたにいても、待っても何も変わらない
  assert.deepEqual(ids({ mobs: [husk], sky: 15 }), [])
  assert.deepEqual(ids({ mobs: [husk], health: 12 }), ['wait inside to heal'])
  assert.deepEqual(ids({ mobs: [skeletonAtDoor], sky: 15 }), ['wait inside while they burn'])
  assert.deepEqual(ids({ mobs: [skeletonAtDoor], sky: 7 }), [])
})

test('夜でも、危なさが低ければ（体力が満タンで失うものがない）外での作業を控えない', () => {
  const { candidates, withheld } = run({ time: NIGHT, mobs: [] })
  assert.ok(candidates.some((c) => c.verb === 'dig'))
  assert.equal(withheld, null)
})

test('夜に危なさが極めて高ければ、外での作業も出口も出さず、朝を待つことだけを出す', () => {
  const { candidates, withheld } = run({ time: NIGHT, mobs: [], health: 4, items: VALUABLE })
  // 体力が低いので、治るまで待つ手も出る。外での作業と出口は出ない
  assert.deepEqual(candidates.map((c) => c.verb), ['wait', 'wait'])
  assert.ok(candidates.some((c) => c.id === 'wait inside until morning'))
  assert.match(withheld, /staying inside: 1 actions outside are held back \(risk extreme/)
})

test('夜の家の中にベッドがあれば、待たずに寝る候補を出す（収集の小目標でも）', () => {
  const ids = (opts) => run({ time: NIGHT, mobs: [], ...opts }).candidates.map((c) => c.id)
  // （危なさが極めて高い夜。低いなら外での作業も出るので、寝る・待つが出ることを確かめる）
  const danger = { health: 4, items: VALUABLE }
  const sleeping = (opts) => ids({ ...danger, ...opts }).filter((id) => id !== 'wait inside to heal')
  assert.deepEqual(sleeping({ bed: true }), ['sleep in the bed'])
  assert.deepEqual(sleeping({ bed: true, status: { leaves: [{ kind: 'sleep' }] } }), ['sleep in the bed'])
  // ベッドがなければ待つ、敵がそばにいれば眠れないので待つ、眠れる時刻の前（夕方）も待つ
  assert.deepEqual(sleeping({ bed: false }), ['wait inside until morning'])
  assert.deepEqual(sleeping({ bed: true, mobs: [skeletonAtDoor] }), ['wait inside until morning'])
  assert.deepEqual(sleeping({ bed: true, time: 12000 }), ['wait inside until morning'])
  // 危なさが低ければ、寝る候補が外での作業と並ぶ
  assert.ok(ids({ bed: true }).includes('sleep in the bed'))
})

test('外でしたいことがなければ、何も控えない', () => {
  const { withheld } = run({ status: { leaves: [] } })
  assert.equal(withheld, null)
})

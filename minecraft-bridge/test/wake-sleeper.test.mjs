import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { isSleeping, sleepersInBeds, sleeperOf } from '../src/sleepers.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { ground } from '../src/candidates.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const v = (x, y, z) => new Vec3(x, y, z)
const KEYS = md.entitiesByName.villager.metadataKeys
const meta = (over) => { const m = []; for (const [k, val] of Object.entries(over)) m[KEYS.indexOf(k)] = val; return m }

// ベッド (5,64,5)（足）と (6,64,5)（頭）に、村人 1 人。asleep: 寝ているか
function room ({ asleep = true, time = 18000, name = 'villager' } = {}) {
  const villager = { id: 77, name, type: 'passive', height: 1.95, position: v(6.5, 64.56, 5.5), metadata: meta(asleep ? { pose: 2, sleeping_pos: { x: 6, y: 64, z: 5 } } : { pose: 0 }) }
  const entity = { position: v(3.5, 64, 5.5), eyeHeight: 1.62, onGround: true }
  const hits = []
  const bot = {
    entity,
    entities: { 0: entity, 77: villager },
    registry: md,
    time: { timeOfDay: time },
    health: 20,
    food: 20,
    heldItem: { name: 'stone_sword' },
    blockAt: (p) => {
      const isBed = (p.x === 5 || p.x === 6) && p.y === 64 && p.z === 5
      return isBed ? { name: 'red_bed', boundingBox: 'block', position: p } : p.y < 64 ? { name: 'oak_planks', boundingBox: 'block', position: p, shapes: [[0, 0, 0, 1, 1, 1]] } : { name: 'air', boundingBox: 'empty', position: p, shapes: [] }
    },
    world: { getBlock: () => ({ shapes: [] }) },
    inventory: { items: () => [] },
    on: () => {},
    off: () => {},
    waitForTicks: async () => {},
    lookAt: async () => {},
    unequip: async () => { bot.heldItem = null },
    attack: (e) => { hits.push({ target: e.id, held: bot.heldItem?.name ?? null }); e.metadata = meta({ pose: 0 }) },
    pathfinder: { goto: async () => {}, setGoal: () => {} },
    sleep: async () => { if (asleep && villager.metadata[KEYS.indexOf('pose')] === 2) throw new Error('the bed is occupied') }
  }
  return { bot, villager, hits }
}

test('ポーズが「寝ている」か、寝ているベッドの位置があれば寝ている。起きている村人は違う', () => {
  assert.equal(isSleeping(room().bot, room().villager), true)
  const r = room({ asleep: false })
  assert.equal(isSleeping(r.bot, r.villager), false)
  assert.equal(isSleeping(r.bot, { name: 'unknown_thing', metadata: [] }), false) // 読み方が分からないものは寝ていない
})

test('ベッドの上で寝ているモブを、そのベッドの位置つきで返す。ベッドの上でなければ返さない', () => {
  const { bot, villager } = room()
  const [s] = sleepersInBeds(bot)
  assert.equal(s.e.id, 77)
  assert.deepEqual([s.bed.x, s.bed.y, s.bed.z], [6, 64, 5])
  assert.equal(sleeperOf(bot, v(5, 64, 5)).e.id, 77) // ベッドのもう半分でも同じ相手
  villager.position = v(9.5, 64, 9.5) // ベッドから離れた所で寝ている（床の上）
  assert.equal(sleepersInBeds(bot).length, 0)
})

const offers = (bot) => ground(bot, { home: null, plan: null, unreachableDrops: new Set(), memory: { deaths: [], places: {}, explored: {}, chests: {}, furnaces: {} } },
  new Knowledge(md), { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.filter((c) => c.verb === 'wake_sleeper')

test('夜、ベッドで寝ている村人を、1 回殴って起こす手として出す。attack ではない。昼は出さない', () => {
  const [c] = offers(room().bot)
  assert.equal(c.id, 'wake up the sleeping villager #77 in the bed at 6,64,5 (one hit)')
  assert.equal(c.entityId, 77)
  assert.equal(offers(room({ time: 1000 }).bot).length, 0)
  assert.equal(offers(room({ asleep: false }).bot).length, 0)
})

test('起こす: 素手で 1 回だけ殴り、起きればベッドが空いたと返す', async () => {
  const { bot, hits } = room()
  const signal = new AbortController().signal
  const result = await PRIMITIVES.wake_sleeper(bot, {}, { entityId: 77, target: 'villager' }, signal)
  assert.deepEqual(hits, [{ target: 77, held: null }]) // 剣は外して、1 回
  assert.match(result, /woke the villager #77 up with one hit/)
})

test('もう起きていれば殴らない。殴っても起きなければ失敗にする', async () => {
  const signal = new AbortController().signal
  const awake = room({ asleep: false })
  assert.match(await PRIMITIVES.wake_sleeper(awake.bot, {}, { entityId: 77, target: 'villager' }, signal), /already awake/)
  assert.equal(awake.hits.length, 0)
  const heavy = room()
  heavy.bot.attack = () => {} // 起きない
  await assert.rejects(PRIMITIVES.wake_sleeper(heavy.bot, {}, { entityId: 77, target: 'villager' }, signal), /still asleep/)
})

test('寝ようとして断られたら、誰が寝ているかと、殴って起こす手を添える', async () => {
  const { bot } = room()
  const home = { door: v(2, 64, 5), inside: v(3, 64, 5), outside: v(1, 64, 5), min: v(3, 64, 4), max: v(4, 64, 6), bed: v(5, 64, 5), breach: [] }
  await assert.rejects(
    PRIMITIVES.sleep_at(bot, { home }, { pos: v(5, 64, 5) }, new AbortController().signal),
    /the bed is occupied: a villager #77 is sleeping in it\. Hit it once to wake it \(the option "wake up the sleeping villager"\)/
  )
})

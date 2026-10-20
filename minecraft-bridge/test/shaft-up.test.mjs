import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { closedSpace, isolatedSpace, pillarUnder } from '../src/reach.mjs'
import { forNeeds, ground } from '../src/candidates.mjs'
import { sharedState } from '../src/state.mjs'
import { noteRefusal } from '../src/refusals.mjs'
import { standingCell } from '../src/observe.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { diggable } from '../src/world.mjs'
import { registerFacility } from '../src/facilities.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')

// y 70 まで石。立てる縦穴は (0, 50..53, 0)（床 y49）。(1, 52..53, 0) は横の張り出し（床 y51）で、その先 x>=2 は読み込まれていない
// （歩ける先に未読み込みがあれば、閉じていない）。blocks で上書き
function world (blocks = {}) {
  const set = new Map(Object.entries(blocks))
  const air = [[0, 50], [0, 51], [0, 52], [0, 53], [1, 52], [1, 53]]
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    if (q.x >= 2) return null
    const name = set.get(`${q.x},${q.y},${q.z}`) ?? (q.z === 0 && air.some(([x, y]) => x === q.x && y === q.y) ? 'air' : q.y > 70 ? 'air' : 'stone')
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function botIn (w, { at = new Vec3(0.5, 50, 0.5), items = {} } = {}) {
  const held = { ...items }
  let jumping = false
  return {
    registry: md,
    entities: {},
    entity: { position: at, onGround: true, yaw: 0, isInWater: false },
    time: { timeOfDay: 1000, age: 0 },
    health: 20,
    food: 20,
    blockAt: w.blockAt,
    findBlock: () => null,
    findBlocks: () => [],
    heldItem: null,
    inventory: { items: () => Object.entries(held).filter(([, n]) => n > 0).map(([name, count]) => ({ name, count, type: md.itemsByName[name].id })), slots: [] },
    equip: async () => {},
    unequip: async () => {},
    lookAt: async () => {},
    look: async () => {},
    setControlState: (k, on) => { if (k === 'jump') jumping = on },
    waitForTicks: async () => { if (jumping) at.y = Math.floor(at.y) + 1.2 },
    dig: async (block) => { const p = block.position; w.set.set(`${p.x},${p.y},${p.z}`, 'air') },
    placeBlock: async (ref, face) => {
      const p = ref.position.plus(face)
      assert.equal(w.blockAt(p).name, 'air', 'placing into a filled cell')
      w.set.set(`${p.x},${p.y},${p.z}`, 'dirt')
      held.dirt -= 1
    }
  }
}

const signal = new AbortController().signal
const k = new Knowledge(md)
const state = () => ({ home: null, formerHomes: [], builds: {}, plan: null })

// 閉じていない地下でも、地上へ出る道が見つからなければ、真上へ縦穴を掘り上がる（設計書 178）。
// 頭の上の岩は素手で掘る（つるはしなし）

test('shaft_up: 頭の上の岩を素手で掘って足元に積み、地上まで出る（1 手に SHAFT_STEP まで）', async () => {
  const w = world()
  const bot = botIn(w, { items: { dirt: 40 } })
  const st = state()
  const text = await PRIMITIVES.shaft_up(bot, st, {}, signal)
  assert.match(text, /piled up \d+ blocks? and now stand at/)
  assert.ok(bot.entity.position.y > 50)
  const feet = Math.floor(bot.entity.position.y)
  assert.equal(w.blockAt(new Vec3(0, feet - 1, 0)).name, 'dirt') // 足元は積んだ土
  assert.equal(w.blockAt(new Vec3(0, feet + 1, 0)).name, 'air') // 頭の上は掘った
  assert.ok(text.includes('out of the ground') || st.shaftUpAt > 0, text)
})

test('shaft_up: 積むものがなければ、理由を言い、しばらく出さない印を付ける', async () => {
  const st = state()
  await assert.rejects(PRIMITIVES.shaft_up(botIn(world()), st, {}, signal), /ran out of dirt and cobblestone/)
  assert.ok(st.shaftBlockedUntil > Date.now())
})

test('shaft_up: 頭の上の岩の隣に水があれば掘らない', async () => {
  const w = world({ '1,52,0': 'water', '0,52,0': 'stone' })
  const st = state()
  await assert.rejects(PRIMITIVES.shaft_up(botIn(w, { items: { dirt: 40 } }), st, {}, signal), /water at 1,52,0/)
  assert.ok(st.shaftBlockedUntil > Date.now())
})

test('地下で surface が失敗したあと、積む物があれば shaft_up を出す。失敗していなければ出さない', () => {
  const bot = botIn(world(), { items: { cobblestone: 20 } })
  const none = forNeeds(bot, state(), k).find((c) => c.verb === 'shaft_up')
  assert.equal(none, undefined)
  const st = { ...state(), surfaceFailedAt: Date.now() }
  const a = forNeeds(bot, st, k).find((c) => c.verb === 'shaft_up')
  assert.ok(a, 'offered')
  assert.equal(a.urgent, true) // 昼
  assert.match(a.purpose, /no pickaxe/)
  bot.time.timeOfDay = 15000
  assert.ok(!forNeeds(bot, st, k).find((c) => c.verb === 'shaft_up').urgent) // 夜は選べる手
  bot.time.timeOfDay = 1000
  assert.equal(forNeeds(botIn(world(), { items: { cobblestone: 5 } }), st, k).some((c) => c.verb === 'shaft_up'), false) // 積む物が足りない
  assert.equal(forNeeds(bot, { ...st, shaftBlockedUntil: Date.now() + 60000 }, k).some((c) => c.verb === 'shaft_up'), false)
})

test('掘り上がりの途中（直前に shaft_up で上がった）なら、surface の失敗がなくても続きを出す。夜も urgent', () => {
  const bot = botIn(world(), { items: { cobblestone: 20 } })
  bot.time.timeOfDay = 15000
  const a = forNeeds(bot, { ...state(), shaftUpAt: Date.now() }, k).find((c) => c.verb === 'shaft_up')
  assert.ok(a && a.urgent)
})

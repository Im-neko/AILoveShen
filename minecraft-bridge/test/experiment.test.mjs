// 行き詰まったときの実験の道具（設計書 85）: 素手・面・スニークを指定できる use_item、1 マスを細かく見る inspect、
// ブリッジが残した失敗の記録を読む diagnostics
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { createTools } from '../src/tools.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import { noteRefusal } from '../src/refusals.mjs'
import { lightWorld } from './light-world.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }

function world (doorProps = { open: 'false' }) {
  const props = { ...doorProps }
  const at = (p) => {
    if (p.x === 2 && p.z === 4 && (p.y === 70 || p.y === 71)) return { name: 'oak_door', boundingBox: props.open === 'true' ? 'empty' : 'block', position: p, getProperties: () => ({ ...props, facing: 'south' }) }
    if (p.y < 70) return { name: 'stone', boundingBox: 'block', position: p, getProperties: () => ({}) }
    return { name: 'air', boundingBox: 'empty', position: p, getProperties: () => ({}) }
  }
  return { props, at }
}

function setup ({ items = [], doorProps } = {}) {
  const w = world(doorProps)
  const me = v(2.5, 70, 5.5)
  const bot = {
    entity: { position: me, eyeHeight: 1.62, yaw: 0, pitch: 0, onGround: true },
    entities: {},
    registry: md,
    time: { timeOfDay: 1000, age: 0 },
    health: 20,
    food: 20,
    controlState: {},
    heldItem: items.length ? { name: items[0][0] } : null,
    blockAt: w.at,
    blockAtCursor: () => w.at(v(2, 70, 4)),
    findBlock: () => null,
    findBlocks: () => [],
    world: { raycast: () => null },
    inventory: { items: () => items.map(([name, count]) => ({ name, count, type: 1 })) },
    setControlState (c, on) { this.controlState[c] = on },
    unequip: async () => { bot.heldItem = null },
    equip: async (item) => { bot.heldItem = item },
    lookAt: async () => {},
    waitForTicks: async () => {},
    activateBlock: async (block, face, cursor) => { bot.clicked = { name: block.name, face, cursor, sneak: !!bot.controlState.sneak, held: bot.heldItem?.name ?? null }; if (bot.opens) w.props.open = w.props.open === 'true' ? 'false' : 'true' }
  }
  const state = { home, formerHomes: [], plan: null, busy: false, reflex: false, memory: {}, unreachableDrops: new Set() }
  const runs = []
  const callTool = createTools({
    bot, state, knowledge: new Knowledge(md),
    run: async (c, label) => { runs.push({ c, label }); return { ok: true, result: 'done', seconds: 0 } },
    candidates: () => [],
    check: () => []
  })
  return { callTool, runs, bot, state, w }
}

test('use_item: item を省くと素手で x,y,z のブロックを押す。家のドアも押せる（壊さないので）', async () => {
  const { callTool, runs } = setup({ items: [['wooden_sword', 1]] })
  const r = await callTool('use_item', { x: 2, y: 70, z: 4, face: 'south', sneak: false })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.deepEqual([runs[0].c.item, runs[0].c.face, runs[0].c.sneak], [null, 'south', false])
  // 持ち物を指定すると、家のドアには使えない（置く・注ぐ物かもしれない）
  const held = await callTool('use_item', { item: 'wooden_sword', x: 2, y: 70, z: 4 })
  assert.equal(held.refused, true)
  assert.match(held.result, /current home/)
})

test('use_item: target: home_door は家のドアの座標になる（覚えた候補にできる）。ほかの target は断る', async () => {
  const { callTool, runs } = setup()
  const r = await callTool('use_item', { target: 'home_door', face: 'south', sneak: false })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.deepEqual([runs[0].c.pos.x, runs[0].c.pos.y, runs[0].c.pos.z], [2, 70, 4])
  assert.match((await callTool('use_item', { target: 'garage' })).result, /target must be home_door/)
})

test('use_item: 素手には座標が要る。相手（entity）は取れない。面の名前は決まっている', async () => {
  const { callTool } = setup()
  assert.match((await callTool('use_item', {})).result, /x, y, z/)
  assert.match((await callTool('use_item', { entity: 3, x: 2, y: 70, z: 4 })).result, /need an item/)
  assert.match((await callTool('use_item', { x: 2, y: 70, z: 4, face: 'left' })).result, /face must be one of/)
})

test('素手で押す: 手を空にし、指定した面を押し、スニークを元に戻す。ドアが開いた変化を返す', async () => {
  const { bot, state } = setup({ items: [['wooden_sword', 1]] })
  bot.opens = true
  bot.controlState.sneak = true
  const c = { verb: 'use_item', item: null, pos: v(2, 70, 4), face: 'south', sneak: false }
  const result = await PRIMITIVES.use_item(bot, state, c, new AbortController().signal)
  assert.equal(bot.clicked.held, null, '手は空')
  assert.equal(bot.clicked.sneak, false)
  assert.deepEqual([bot.clicked.face.x, bot.clicked.face.y, bot.clicked.face.z], [0, 0, 1])
  assert.deepEqual([bot.clicked.cursor.x, bot.clicked.cursor.z], [0.5, 1])
  assert.match(result, /open: false -> true/)
  assert.equal(bot.controlState.sneak, false, 'スニークを触ったら、終わりに外す')
})

test('素手で押して何も変わらなければ失敗（理由に、何もしなかったと書く）', async () => {
  const { bot, state } = setup()
  bot.opens = false
  await assert.rejects(() => PRIMITIVES.use_item(bot, state, { verb: 'use_item', item: null, pos: v(2, 70, 4) }, new AbortController().signal), /nothing changed after using an empty hand/)
})

test('inspect: 座標がなければ自分の今、あればそのマスの状態・隣・届くか・断られる理由', async () => {
  const { callTool, state } = setup({ items: [['wooden_sword', 1]], doorProps: { open: 'false' } })
  const me = await callTool('inspect', {})
  assert.equal(me.ok, true)
  assert.equal(me.result.self.held, 'wooden_sword')
  const cell = (await callTool('inspect', { x: 2, y: 70, z: 4 })).result
  assert.equal(cell.block, 'oak_door')
  assert.equal(cell.props.open, 'false')
  assert.equal(cell.neighbours.down, 'stone')
  assert.equal(cell.within_reach, true)
  assert.match(cell.protected, /current home/)
  assert.deepEqual(cell.refused_before, [])
  noteRefusal(state, 'use_item', v(2, 70, 4), 'No path to there')
  noteRefusal(state, 'dig', v(2, 70, 4), 'No path to there')
  const again = (await callTool('inspect', { x: 2, y: 70, z: 4 })).result
  assert.deepEqual(again.refused_before.map((r) => r.tool).sort(), ['dig', 'use_item'])
})

test('inspect: そのマスの光（ブロックの光と空の光）も言う。サーバーの光が届いていなければ null', async () => {
  const { callTool, bot } = setup()
  const none = (await callTool('inspect', { x: 2, y: 70, z: 4 })).result
  assert.deepEqual([none.light, none.sky_light], [null, null])
  bot.world = lightWorld((x, y, z) => (x === 2 && y === 70 && z === 4 ? 13 : 0), (x, y) => (y >= 70 ? 15 : 0))
  const lit = (await callTool('inspect', { x: 2, y: 70, z: 4 })).result
  assert.deepEqual([lit.light, lit.sky_light], [13, 15])
  const dark = (await callTool('inspect', { x: 2, y: 69, z: 4 })).result
  assert.deepEqual([dark.light, dark.sky_light], [0, 0])
})

test('diagnostics: 種類ごとの最後の記録を読む。種類を指定すれば件数も', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diag-'))
  const file = path.join(dir, 'door.jsonl')
  fs.writeFileSync(file, [{ at: new Date().toISOString(), opened: false, wanted: 'open' }, { at: new Date().toISOString(), opened: true, wanted: 'close' }].map((r) => JSON.stringify(r)).join('\n') + '\n')
  process.env.DOOR_TOGGLES_FILE = file
  process.env.PLACE_REFUSALS_FILE = path.join(dir, 'none.jsonl')
  process.env.STUCK_WALKS_FILE = path.join(dir, 'none2.jsonl')
  const { callTool } = setup()
  const all = (await callTool('diagnostics', {})).result
  assert.equal(all.door.records.length, 1)
  assert.equal(all.door.records[0].wanted, 'close')
  assert.deepEqual(all.place.records, [])
  const two = (await callTool('diagnostics', { kind: 'door', n: 2 })).result
  assert.equal(two.door.records.length, 2)
  assert.match((await callTool('diagnostics', { kind: 'x' })).result, /unknown kind/)
})

test('inspect {}: 閉じた所にいるか、家のドアまでどれだけ離れているかも言う（ドアが開かないのか、道がないのかを分ける）', async () => {
  const { callTool } = setup()
  const me = (await callTool('inspect', {})).result.self
  assert.ok('closed_space' in me)
  assert.ok(me.home_door && typeof me.home_door.horizontal_m === 'number' && typeof me.home_door.below_m === 'number', JSON.stringify(me))
})

import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { closedSpace, isolatedSpace, pillarUnder } from '../src/reach.mjs'
import { forNeeds, ground } from '../src/candidates.mjs'
import { sharedState, rawState } from '../src/state.mjs'
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

test('縦穴の底は閉じた所。1 段積むと張り出しに上がれて、閉じていない', () => {
  const bot = botIn(world())
  assert.deepEqual(closedSpace(bot), { cells: 1 })
  const up = botIn(world({ '0,50,0': 'dirt' }), { at: new Vec3(0.5, 51, 0.5) })
  assert.equal(closedSpace(up), null)
})

test('climb_out: 足元に土を積んで、歩いて出られる高さまで上がる（道具もつるはしもいらない）', async () => {
  const w = world()
  const bot = botIn(w, { items: { dirt: 4 } })
  const text = await PRIMITIVES.climb_out(bot, state(), {}, signal)
  assert.match(text, /piled up 1 block and can walk out now \(at 0,51,0\)/)
  assert.equal(w.blockAt(new Vec3(0, 50, 0)).name, 'dirt')
})

test('climb_out: 頭の上が石なら素手で掘って上がる（閉じ込められたときだけ許す）', async () => {
  const w = world({ '0,52,0': 'stone' })
  const bot = botIn(w, { items: { dirt: 4 } })
  const text = await PRIMITIVES.climb_out(bot, state(), {}, signal)
  assert.match(text, /can walk out now/)
  assert.equal(w.blockAt(new Vec3(0, 52, 0)).name, 'air')
})

test('climb_out: 積むものがなければ、積めたところまでと理由を言う', async () => {
  const bot = botIn(world())
  await assert.rejects(PRIMITIVES.climb_out(bot, state(), {}, signal), /ran out of dirt and cobblestone/)
})

test('climb_out: 閉じていなければ何もしない', async () => {
  const bot = botIn(world({ '0,50,0': 'dirt' }), { at: new Vec3(0.5, 51, 0.5), items: { dirt: 4 } })
  assert.match(await PRIMITIVES.climb_out(bot, state(), {}, signal), /not shut in/)
})

test('閉じ込められて土か丸石があれば、昼は urgent の climb_out を、夜は urgent なしで出す。積む物がなければ出さない', () => {
  const day = botIn(world(), { items: { dirt: 4 } })
  const a = forNeeds(day, state(), k).find((c) => c.verb === 'climb_out')
  assert.ok(a, 'offered')
  assert.equal(a.urgent, true)
  const night = botIn(world(), { items: { cobblestone: 4 } })
  night.time.timeOfDay = 15000
  const b = forNeeds(night, state(), k).find((c) => c.verb === 'climb_out')
  assert.ok(b && !b.urgent)
  assert.equal(forNeeds(botIn(world()), state(), k).some((c) => c.verb === 'climb_out'), false)
})

test('閉じていない所では出さない', () => {
  const bot = botIn(world({ '0,50,0': 'dirt' }), { at: new Vec3(0.5, 51, 0.5), items: { dirt: 4 } })
  assert.equal(forNeeds(bot, state(), k).some((c) => c.verb === 'climb_out'), false)
})

test('採掘場の階段の柱と段の床は、探して掘る対象にならない', () => {
  const s = { home: { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3) }, plan: null, builds: {}, formerHomes: [] }
  const bot = { registry: md, entity: { position: new Vec3(10, 64, 0), onGround: true }, blockAt: (p) => { const b = Block.fromProperties(p.y > 63 ? 'air' : 'stone', {}, 0); b.position = p; return b }, findBlocks: () => [] }
  const fac = registerFacility(bot, s, 'mine_1', { kind: 'mine', boxes: [{ min_x: 8, min_z: 0, max_x: 15, max_z: 7 }], design: { bottom_y: 40, tunnels: 2, tunnel_length: 8 } })
  const step = fac.layout.cells.filter((c) => c.part === 'stairs')[3]
  const floor = new Vec3(step.x, step.y - 1, step.z)
  assert.equal(diggable({ ...bot, blockAt: (p) => { const b = Block.fromProperties('stone', {}, 0); b.position = p; return b } }, s, 'stone', floor), false)
})

test('つるはしが壊れて持っていないとき、家から遠ければ家に作りに戻る手を出す（近いときは出さない）', () => {
  const home = { inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), door: new Vec3(0, 64, 2), outside: new Vec3(0, 64, 3), breach: [] }
  const broken = { ...state(), home, brokenTools: [{ item: 'wooden_pickaxe', at: Date.now() }] }
  const far = botIn(world({ '0,50,0': 'dirt' }), { at: new Vec3(0.5, 51, 0.5) })
  const go = forNeeds(far, broken, k).find((c) => c.verb === 'go_home' && /new pickaxe/.test(c.id))
  assert.ok(go, 'offered')
  assert.match(go.id, /the wooden_pickaxe broke/)
  const near = botIn(world(), { at: new Vec3(0.5, 64, 4.5) })
  assert.equal(forNeeds(near, broken, k).some((c) => /new pickaxe/.test(c.id)), false)
  const held = botIn(world({ '0,50,0': 'dirt' }), { at: new Vec3(0.5, 51, 0.5), items: { stone_pickaxe: 1 } })
  assert.equal(forNeeds(held, broken, k).some((c) => /new pickaxe/.test(c.id)), false)
})

test('climb_out: 頭の上の岩の隣に溶岩があれば掘らず、しばらく聞かずに実行しない印を付ける', async () => {
  const w = world({ '0,52,0': 'stone', '1,52,0': 'lava' })
  const bot = botIn(w, { items: { dirt: 4 } })
  const st = state()
  await assert.rejects(PRIMITIVES.climb_out(bot, st, {}, signal), /lava at 1,52,0 beside the rock above/)
  assert.equal(w.blockAt(new Vec3(0, 52, 0)).name, 'stone', '掘っていない')
  assert.ok(st.climbBlockedUntil > Date.now())
})

test('urgent は小さい閉じた所だけ。広いとき、直前に失敗したときは選べる手のまま', () => {
  const big = botIn(world(), { items: { dirt: 4 } })
  // 閉じた広さを 30 マスにする（歩ける所を増やす: 縦穴の横に空気を足す）
  const w = world(Object.fromEntries(Array.from({ length: 30 }, (_, i) => [[`0,50,${i + 1}`, 'air'], [`0,51,${i + 1}`, 'air']]).flat()))
  const wide = botIn(w, { items: { dirt: 4 } })
  assert.ok(closedSpace(wide).cells > 24, String(closedSpace(wide).cells))
  const a = forNeeds(wide, state(), k).find((c) => c.verb === 'climb_out')
  assert.ok(a && !a.urgent, '広いと urgent でない')
  const blocked = { ...state(), climbBlockedUntil: Date.now() + 60000 }
  const b = forNeeds(big, blocked, k).find((c) => c.verb === 'climb_out')
  assert.ok(b && !b.urgent, '失敗の直後は urgent でない')
})

// 水が入った小部屋: 立てる縦穴 (0, 50..53, 0) の 50..52 が水、53 は空気（水面の上の空気）。床 y49
function wetBot (items = { cobblestone: 4 }, extra = {}) {
  const w = world({ '0,50,0': 'water', '0,51,0': 'water', '0,52,0': 'water' })
  const bot = botIn(w, { at: new Vec3(0.5, 50, 0.5), items })
  bot.entity.isInWater = true
  return { w, bot, ...extra }
}

test('水が入った小部屋も閉じた所（水の中でも数える）', () => {
  const { bot } = wetBot()
  assert.ok(closedSpace(bot))
})

test('水の中の climb_out: 下が水なら、横の固い壁に寄りかかって足元に置く。下が固ければ下から', async () => {
  for (const [below, want] of [['water', ['stone', '-1,0,0']], ['stone', ['stone', '0,1,0']]]) {
    const { w, bot } = wetBot()
    w.set.set('0,49,0', below)
    const refs = []
    bot.placeBlock = async (ref, face) => {
      refs.push([ref.name, `${face.x},${face.y},${face.z}`])
      const p = ref.position.plus(face)
      w.set.set(`${p.x},${p.y},${p.z}`, 'cobblestone')
    }
    await PRIMITIVES.climb_out(bot, state(), {}, signal).catch(() => {})
    assert.deepEqual(refs[0], want, `下が ${below}`)
  }
})

test('水の中では、urgent は広さにも昼夜にもよらない。失敗の直後は urgent でない', () => {
  const { bot } = wetBot()
  bot.time.timeOfDay = 18000 // 夜
  const c = forNeeds(bot, state(), k).find((x) => x.verb === 'climb_out')
  assert.ok(c && c.urgent, '水の中は夜でも urgent')
  const blocked = { ...state(), climbBlockedUntil: Date.now() + 60000 }
  assert.ok(!forNeeds(bot, blocked, k).find((x) => x.verb === 'climb_out').urgent)
})

test('climb_out: 溶岩が上にあれば水の中でも掘らない。反射で止められたら失敗の印を付けない', async () => {
  const { w, bot } = wetBot()
  w.set.set('0,52,0', 'stone')
  w.set.set('0,53,0', 'lava')
  const st = state()
  await assert.rejects(PRIMITIVES.climb_out(bot, st, {}, signal), /lava/)
  assert.ok(st.climbBlockedUntil > Date.now())
  const ac = new AbortController()
  const w2 = wetBot()
  w2.bot.waitForTicks = async () => { ac.abort(new Error('reflex')); throw new Error('reflex') }
  const st2 = state()
  await assert.rejects(PRIMITIVES.climb_out(w2.bot, st2, {}, ac.signal))
  assert.equal(st2.climbBlockedUntil, undefined, '中止は失敗ではない')
})


// 平らな地面 (y64 が一番上の固いブロック) に、中央 (0, 65.., 0) から丸石の柱が height 段立つ。まわりは空気
function pillarWorld (height) {
  const set = new Map()
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const name = set.get(`${q.x},${q.y},${q.z}`) ?? (q.y <= 64 ? 'stone' : q.x === 0 && q.z === 0 && q.y <= 64 + height ? 'cobblestone' : 'air')
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function pillarBot (w, height, items = {}) {
  const bot = botIn(w, { at: new Vec3(0.5, 65 + height, 0.5), items })
  bot.dig = async (block) => {
    const p = block.position
    w.set.set(`${p.x},${p.y},${p.z}`, 'air')
    bot.entity.position.y = p.y // 掘った所へ落ちる
  }
  return bot
}

test('柱の頂上は閉じた所ではない（積んでも出られない）。崖の縁として数える', () => {
  const w = pillarWorld(10)
  const bot = pillarBot(w, 10, { dirt: 4 })
  assert.equal(closedSpace(bot), null, '崖がある: 閉じてはいない')
  assert.deepEqual(isolatedSpace(bot), { cells: 1 })
  assert.equal(pillarUnder(bot)?.name, 'cobblestone')
})

test('climb_out は柱の頂上では積まない（何も積まず、閉じていないと言う）', async () => {
  const w = pillarWorld(10)
  const bot = pillarBot(w, 10, { dirt: 4 })
  assert.match(await PRIMITIVES.climb_out(bot, state(), {}, signal), /not shut in/)
  assert.equal(w.blockAt(new Vec3(0, 76, 0)).name, 'air')
})

test('climb_down: 柱を足元から掘って降り、歩いて出られる高さになったら止まる', async () => {
  const w = pillarWorld(10)
  const bot = pillarBot(w, 10)
  const text = await PRIMITIVES.climb_down(bot, state(), {}, signal)
  assert.match(text, /dug down 7 blocks of the pillar and can walk off now \(at 0,68,0\)/)
  assert.equal(isolatedSpace(bot), null)
})

test('climb_down: 自然の地面（横に壁がある所）と、下に水が混じる柱は掘らない', async () => {
  const w = pillarWorld(10)
  w.set.set('1,70,0', 'stone') // 柱の横に壁
  const bot = pillarBot(w, 10)
  await assert.rejects(PRIMITIVES.climb_down(bot, state(), {}, signal), /not a free-standing pillar/)
  const w2 = pillarWorld(10)
  w2.set.set('0,73,0', 'water') // 3 段下に水が混じる柱は、足場にならない
  await assert.rejects(PRIMITIVES.climb_down(pillarBot(w2, 10), state(), {}, signal), /not a free-standing pillar/)
})

test('柱の頂上では climb_down の候補が出る（昼は urgent、失敗の直後は urgent でない）。climb_out は出ない', () => {
  const w = pillarWorld(10)
  const bot = pillarBot(w, 10, { dirt: 4 })
  const c = forNeeds(bot, state(), k)
  assert.ok(!c.find((x) => x.verb === 'climb_out'))
  const down = c.find((x) => x.verb === 'climb_down')
  assert.ok(down && down.urgent)
  const again = forNeeds(bot, { ...state(), descendBlockedUntil: Date.now() + 60000 }, k).find((x) => x.verb === 'climb_down')
  assert.ok(again && !again.urgent)
})

test('閉じた所にいるとき、まわりの状態（surroundings）が歩ける範囲の広さを言う。出られる所と家の中では言わない', () => {
  const shut = sharedState(botIn(world()), state(), () => [])
  assert.deepEqual(shut.surroundings.closed_space, { cells: 1 })
  const free = sharedState(botIn(world({ '0,50,0': 'dirt' }), { at: new Vec3(0.5, 51, 0.5) }), state(), () => [])
  assert.equal('closed_space' in free.surroundings, false)
})

test('積んで出るのに失敗した場所では、その手を提案に出さず、理由を withheld に言う', () => {
  const bot = botIn(world(), { items: { dirt: 20 } })
  const st = state()
  const before = ground(bot, st, k, { dig: () => [], hunt: () => [] }, { leaves: [] })
  assert.ok(before.candidates.some((c) => c.verb === 'climb_out'), before.candidates.map((c) => c.id).join('; '))
  noteRefusal(st, 'climb_out', standingCell(bot), 'failed: could not jump up from the hole at (0, 50, 0)')
  const after = ground(bot, st, k, { dig: () => [], hunt: () => [] }, { leaves: [] })
  assert.ok(!after.candidates.some((c) => c.verb === 'climb_out'))
  assert.match(after.withheld ?? '', /climb out.*could not jump up/)
})

test('判断なしの見え方（rawState）は、閉じた所・家の中・欲求・断りの助言を持たない（設計書 191）', () => {
  const bot = botIn(world())
  const full = sharedState(bot, state(), () => ['torch: go back and make one'])
  assert.ok(full.surroundings.closed_space && full.needs.length && 'in_home' in full.self)
  const raw = rawState(bot)
  assert.deepEqual(Object.keys(raw).sort(), ['inventory', 'mobs', 'self', 'surroundings', 'time', 'tool_wear'])
  assert.deepEqual(Object.keys(raw.surroundings).sort(), ['grid', 'legend'])
  assert.equal('in_home' in raw.self, false)
  assert.equal(raw.self.health, 20)
  assert.equal('deaths' in raw, false)
  const died = rawState(bot, { ...state(), deaths: 3, lastDeath: { phase: 'night', position: { x: 1, y: 64, z: 2 }, damage: { zombie: 5 }, place: 'outside', doing: ['x'] } })
  assert.deepEqual(died.deaths, { count: 3, last: { time: 'night', position: { x: 1, y: 64, z: 2 }, damage_by: { zombie: 5 } } })
})


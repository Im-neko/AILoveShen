import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { levelStatus, levelTarget, columnWork, setLevelWanted, noteLevelZone, keptGround, levelZones } from '../src/leveling.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { createTools } from '../src/tools.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')

// 地面は y 63 まで土（草は 63）。立つ高さは y 64。blocks: { 'x,y,z': name } で上書き
function world (blocks = {}) {
  const set = new Map(Object.entries(blocks))
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const k = `${q.x},${q.y},${q.z}`
    const name = set.get(k) ?? (q.y < 63 ? 'dirt' : q.y === 63 ? 'grass_block' : 'air')
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

const state = (extra = {}) => ({ home: null, formerHomes: [], builds: {}, plantings: [], unreachableDrops: new Set(), ...extra })

test('where: home（家の床の上）か x,y,z（そこに立つ足の高さ）', () => {
  assert.deepEqual(levelTarget({ home: { inside: new Vec3(1, 65, 2) } }, 'home').y, 65)
  assert.equal(levelTarget({}, '4,70,-3').center.z, -3)
  assert.throws(() => levelTarget({}, 'near_home'), /home or x,y,z/)
  assert.throws(() => levelTarget({}, 'home'), /no home/)
})

test('出っ張りは上から掘り、穴は下から埋める。木・水・畑・置いた物・植えた木は触らない', () => {
  const w = world({
    '2,64,0': 'dirt', '2,65,0': 'grass_block', // 出っ張り（2 段）
    '-2,63,0': 'air', '-2,62,0': 'air', // 穴（2 段）
    '0,64,2': 'short_grass', // 草は地面ではない
    '0,64,-2': 'oak_log', // 木
    '1,63,-2': 'water',
    '-1,63,1': 'farmland',
    '1,64,1': 'chest', // 置いた物
    '-2,64,-2': 'oak_sapling' // 植えた木（まわり 1 マスも）
  })
  const s = levelStatus({ blockAt: w.blockAt }, state({ plantings: [{ x: -2, y: 64, z: -2 }] }), '0,64,0', 3)
  assert.deepEqual(s.cuts.map((p) => `${p.x},${p.y},${p.z}`), ['2,65,0', '2,64,0'])
  assert.deepEqual(s.fills.map((p) => `${p.x},${p.y},${p.z}`), ['-2,62,0', '-2,63,0'])
  assert.equal(s.off, 2)
  assert.equal(s.skipped.tree, 1)
  assert.equal(s.skipped.water, 1)
  assert.equal(s.skipped.farmland, 1)
  assert.equal(s.skipped['chest (not natural ground)'], 1)
  assert.ok(s.skipped['planted tree'] >= 4)
  assert.equal(columnWork({ blockAt: w.blockAt }, state(), 0, 2, 64).off, false)
})

test('家と建物（とまわり 1 マス）は触らない。家の床と同じ高さなら壁のすぐ外は整地する。見えていない列はそろっていないと数える', () => {
  const w = world({ '0,64,0': 'dirt' })
  const home = { door: new Vec3(0, 64, 3), inside: new Vec3(0, 64, 0), min: new Vec3(-1, 64, -1), max: new Vec3(1, 64, 1), breach: [] }
  assert.equal(columnWork({ blockAt: w.blockAt }, state({ home }), 0, 0, 64), 'house or build')
  assert.equal(columnWork({ blockAt: w.blockAt }, state({ home }), 2, 0, 64), 'house or build') // 壁の下
  assert.equal(typeof columnWork({ blockAt: w.blockAt }, state({ home }), 3, 0, 64), 'object') // 家の床と同じ高さなら、壁のすぐ外は整地する
  assert.equal(columnWork({ blockAt: w.blockAt }, state({ home }), 3, 0, 65), 'house or build') // 別の高さなら触らない
  const blind = levelStatus({ blockAt: (p) => (p.x > 2 ? null : w.blockAt(p)) }, state(), '0,64,0', 3)
  assert.ok(blind.unloaded > 0)
})

function fakeBot (w, items) {
  const inv = items.map(([name, count]) => ({ name, count, type: md.itemsByName[name].id }))
  return {
    registry: md,
    entity: { position: new Vec3(0.5, 64, 0.5), onGround: true, effects: {} },
    entities: {},
    blockAt: w.blockAt,
    inventory: { items: () => inv.filter((i) => i.count > 0), emptySlotCount: () => 30 },
    heldItem: null,
    equip: async (item) => {},
    waitForTicks: async () => {},
    dig: async (b) => { w.set.set(`${b.position.x},${b.position.y},${b.position.z}`, 'air'); const d = inv.find((i) => i.name === 'dirt'); if (d) d.count++; else inv.push({ name: 'dirt', count: 1, type: md.itemsByName.dirt.id }) },
    placeBlock: async (below, v) => {
      const p = below.position.plus(v)
      const d = inv.find((i) => i.name === 'dirt' && i.count > 0)
      d.count--
      w.set.set(`${p.x},${p.y},${p.z}`, 'dirt')
    }
  }
}

test('level: 1 回の行動で、先に出っ張りを掘って出た土で穴を埋め、そろえる', async () => {
  const w = world({ '2,64,0': 'dirt', '2,65,0': 'dirt', '-2,63,0': 'air', '-2,62,0': 'air' })
  const bot = fakeBot(w, [])
  const r = await PRIMITIVES.level(bot, state(), { where: '0,64,0', radius: 3 }, new AbortController().signal)
  assert.match(r, /dug 2 blocks, filled 2/)
  const s = levelStatus(bot, state(), '0,64,0', 3)
  assert.equal(s.off, 0)
})

test('level: 何もそろえられなければ失敗（土がなく、穴だけ）。道具は理由を添えて断る', async () => {
  const w = world({ '-2,63,0': 'air' })
  const bot = fakeBot(w, [])
  await assert.rejects(PRIMITIVES.level(bot, state(), { where: '0,64,0', radius: 3 }, new AbortController().signal), /could not level any.*no dirt/)
  const callTool = createTools({
    bot: { ...bot, time: { timeOfDay: 1000 } },
    state: { ...state(), memory: {}, busy: false, reflex: false },
    knowledge: new Knowledge(md),
    run: async (c) => ({ ok: true, result: c.verb, seconds: 0 }),
    candidates: () => [],
    check: () => []
  })
  assert.match((await callTool('level', { where: '0,64,0', radius: 3 })).result, /carry 0 dirt/)
  assert.match((await callTool('level', { where: '0,64,0', radius: 2 })).result, /radius must be 3-16/)
  const flat = createTools({ bot: { ...fakeBot(world(), []), time: { timeOfDay: 1000 } }, state: { ...state(), memory: {}, busy: false, reflex: false }, knowledge: new Knowledge(md), run: async () => ({}), candidates: () => [], check: () => [] })
  assert.match((await flat('level', { where: '0,64,0', radius: 3 })).result, /already level/)
})

test('leveled(where, distance): 出っ張りがあれば level の候補、穴だけで土がなければ土を集める。そろえば達成', async () => {
  const { makeGoal, evaluate } = await import('../src/goals.mjs')
  const { ground } = await import('../src/candidates.mjs')
  const k = new Knowledge(md)
  const worldInfo = { inventory: {}, stored: {}, smelting: {}, blocks: {}, mobs: {}, table: 'near', furnace: null, unlocked: () => true, remembered: new Set(), dig: () => [], hunt: () => [] }
  const botOf = (w, items = []) => ({ ...fakeBot(w, items), time: { timeOfDay: 1000 } })
  const st = state()
  assert.throws(() => makeGoal({ predicate: 'leveled', where: '0,64,0', distance: 40 }, botOf(world()), st, k), /3-16/)
  const goal = makeGoal({ predicate: 'leveled', where: '0,64,0', distance: 3 }, botOf(world()), st, k)

  const bumpy = botOf(world({ '2,64,0': 'dirt', '-2,63,0': 'air' }))
  const r = evaluate(bumpy, { ...st, goal }, k, worldInfo)
  assert.equal(r.met, false)
  assert.equal(r.remaining, 2)
  assert.match(r.lines[0], /2 off \(1 blocks to dig, 1 to fill\)/)
  const [c] = ground(bumpy, { ...st, goal, home: null }, k, worldInfo, r).candidates.filter((x) => x.verb === 'level')
  assert.deepEqual([c.id, c.where, c.radius], ['level the ground within 3 of 0,64,0', '0,64,0', 3])

  const holes = evaluate(botOf(world({ '-2,63,0': 'air' })), { ...st, goal }, k, worldInfo)
  assert.ok(!holes.leaves.some((l) => l.kind === 'level'))
  assert.ok(holes.lines.some((l) => /dirt/.test(l)), holes.lines.join('\n'))

  assert.equal(evaluate(botOf(world()), { ...st, goal }, k, worldInfo).met, true)

  // 石の出っ張りだけ: ツルハシがなければ作る（素手では掘っても何も落ちない）
  const stone = evaluate(botOf(world({ '2,64,0': 'stone' })), { ...st, goal }, k, worldInfo)
  assert.ok(stone.lines.includes('1 blocks to dig need a wooden_pickaxe (or better)'), stone.lines.join('\n'))
  assert.ok(!stone.leaves.some((l) => l.kind === 'level'))
  const withPick = evaluate(botOf(world({ '2,64,0': 'stone' }), [['wooden_pickaxe', 1]]), { ...st, goal }, k, worldInfo)
  assert.ok(withPick.leaves.some((l) => l.kind === 'level'))
})

test('level: 穴の中に立っていたら、足元に土を積んで登って埋める（深い穴も 1 段ずつ）', async () => {
  const w = world({ '-2,63,0': 'air', '-2,62,0': 'air' })
  const bot = fakeBot(w, [['dirt', 4]])
  bot.entity.position = new Vec3(-1.5, 62, 0.5) // 2 段の穴の底
  const goals = []
  bot.on = () => {}
  bot.off = () => {}
  bot.world = { getBlock: w.blockAt }
  bot.pathfinder = {
    setGoal: () => {},
    // 1 つ上に立つ所へ: 足元（今いるセル）に土を積んで 1 段上がる
    goto: async (g) => {
      goals.push(g.constructor.name)
      const at = bot.entity.position.floored()
      assert.deepEqual([g.x, g.y, g.z], [at.x, at.y + 1, at.z])
      await bot.placeBlock(w.blockAt(at.offset(0, -1, 0)), new Vec3(0, 1, 0))
      bot.entity.position = bot.entity.position.offset(0, 1, 0)
    }
  }
  const r = await PRIMITIVES.level(bot, state(), { where: '0,64,0', radius: 3 }, new AbortController().signal)
  assert.match(r, /filled 2/)
  assert.deepEqual(goals, ['GoalBlock', 'GoalBlock'])
  assert.equal(levelStatus(bot, state(), '0,64,0', 3).off, 0)
})

test('整地する土地の、床より下の地形は掘らない（土は整地の外から）。床より上の出っ張りは掘ってよい', () => {
  const st = state()
  setLevelWanted(st, [{ where: '0,64,0', distance: 5 }, { where: '0,64,0', distance: 99 }, { where: 'home', distance: 4 }])
  // 範囲外の半径と、家がまだない home は、ないものとして読む
  assert.deepEqual(levelZones(st), [{ x: 0, y: 64, z: 0, radius: 5 }])
  assert.equal(keptGround(st, new Vec3(3, 63, 0), 'dirt'), true) // 床（y 63）とその下
  assert.equal(keptGround(st, new Vec3(3, 60, 0), 'stone'), true)
  assert.equal(keptGround(st, new Vec3(6, 63, 0), 'dirt'), true) // 半径の外 1 マスまで
  assert.equal(keptGround(st, new Vec3(7, 63, 0), 'dirt'), false)
  assert.equal(keptGround(st, new Vec3(3, 64, 0), 'dirt'), false) // 出っ張り（整地で掘るもの）
  assert.equal(keptGround(st, new Vec3(3, 63, 0), 'oak_log'), false) // 地形ではない
  // 整地を始めた土地は、予定を送り直しても残る（目標が終わった後も守る）
  noteLevelZone(st, '20,70,20', 6)
  noteLevelZone(st, '20,70,20', 6)
  setLevelWanted(st, [])
  assert.deepEqual(levelZones(st), [{ x: 20, y: 70, z: 20, radius: 6 }])
  assert.equal(keptGround(st, new Vec3(22, 69, 20), 'dirt'), true)
  // 送られていなければ（undefined）予定はそのまま
  setLevelWanted(st, [{ where: '0,64,0', distance: 5 }])
  setLevelWanted(st, undefined)
  assert.equal(levelZones(st).length, 2)
})

test('dig の道具は、整地する土地の下を断り、外を勧める。出っ張りは掘れる', async () => {
  const st = state({ leveling: { done: [{ x: 0, y: 64, z: 0, radius: 5 }], wanted: [] } })
  const w = world({ '2,64,0': 'dirt' })
  const callTool = createTools({
    bot: { ...fakeBot(w, []), time: { timeOfDay: 1000 } },
    state: { ...st, memory: {}, busy: false, reflex: false },
    knowledge: new Knowledge(md),
    run: async (c) => ({ ok: true, result: c.verb, seconds: 0 }),
    candidates: () => [],
    check: () => []
  })
  assert.match((await callTool('dig', { x: 1, y: 63, z: 0 })).result, /being leveled/)
  assert.equal((await callTool('dig', { x: 2, y: 64, z: 0 })).result, 'dig')
})

test('leveled(where, distance): 埋めるだけなら穴の数だけ土を持ってから始める。足りなければ土を集める', async () => {
  const { makeGoal, evaluate } = await import('../src/goals.mjs')
  const k = new Knowledge(md)
  const worldInfo = { inventory: {}, stored: {}, smelting: {}, blocks: {}, mobs: {}, table: 'near', furnace: null, unlocked: () => true, remembered: new Set(), dig: () => [], hunt: () => [] }
  const holes = { '-2,63,0': 'air', '-2,62,0': 'air', '-2,61,0': 'air' } // 3 個の穴
  const botOf = (items) => ({ ...fakeBot(world(holes), items), time: { timeOfDay: 1000 } })
  const st = state()
  const goal = makeGoal({ predicate: 'leveled', where: '0,64,0', distance: 3 }, botOf([]), st, k)
  const few = evaluate(botOf([['dirt', 1]]), { ...st, goal }, k, worldInfo)
  assert.ok(!few.leaves.some((l) => l.kind === 'level'), 'dirt 1 of 3: not yet')
  assert.ok(few.lines.some((l) => /3 blocks to fill need 3 dirt \(carrying 1\)/.test(l)), few.lines.join('\n'))
  const enough = evaluate(botOf([['dirt', 3]]), { ...st, goal }, k, worldInfo)
  assert.ok(enough.leaves.some((l) => l.kind === 'level'))
})

test('level: 浅い穴に立っていて、足元に積まずに外へ出られたら、外から埋める', async () => {
  const w = world({ '-2,63,0': 'air' }) // 1 段の穴
  const bot = fakeBot(w, [['dirt', 2]])
  bot.entity.position = new Vec3(-1.5, 63, 0.5) // 穴の中
  bot.on = () => {}
  bot.off = () => {}
  bot.world = { getBlock: w.blockAt }
  bot.pathfinder = {
    setGoal: () => {},
    // 1 段の穴は、積まずに横の地面へ上がれる。GoalPlaceBlock（縁へ）も足を動かすだけ
    goto: async (g) => { bot.entity.position = new Vec3(-0.5, 64, 0.5) }
  }
  const r = await PRIMITIVES.level(bot, state(), { where: '0,64,0', radius: 3 }, new AbortController().signal)
  assert.match(r, /filled 1/)
  assert.equal(levelStatus(bot, state(), '0,64,0', 3).off, 0)
})

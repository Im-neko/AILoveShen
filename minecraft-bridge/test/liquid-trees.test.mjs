import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { holdsBackLiquid, liquidReason } from '../src/liquid.mjs'
import { isTreeLog, isLeftoverLog, gatherableLog, forgetTrees } from '../src/trees.mjs'
import { recordPlaced, placedByMe, recordDug, leakingHoles, installLedger, DUG_TICKS } from '../src/ledger.mjs'
import { digTargets, reachableLogHeight, dirtForLogHeight } from '../src/world.mjs'
import { aroundMe } from '../src/around.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'
import { ground } from '../src/candidates.mjs'
import { Knowledge } from '../src/knowledge.mjs'
import minecraftData from 'minecraft-data'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const v = (x, y, z) => new Vec3(x, y, z)
const SOLID = /^(stone|grass_block|dirt|oak_log|stripped_oak_log|cobblestone|oak_planks)$/

// y 63 までが地面（草）。extra: 'x,y,z' → ブロック名。それ以外は空気（y > 63）
function world (extra = {}) {
  const cells = new Map(Object.entries(extra))
  const blockAt = (p) => {
    const name = cells.get(`${p.x},${p.y},${p.z}`) ?? (p.y <= 63 ? 'grass_block' : 'air')
    const solid = SOLID.test(name) || name.endsWith('_leaves')
    return { name, position: p, boundingBox: solid ? 'block' : 'empty', shapes: solid ? [[0, 0, 0, 1, 1, 1]] : [], getProperties: () => ({}) }
  }
  const bot = {
    entity: { position: v(0.5, 64, 0.5), onGround: true },
    entities: {},
    time: { age: 100 * 1200 },
    registry: md,
    blockAt,
    findBlocks: ({ matching }) => [...cells].filter(([, n]) => md.blocksByName[n]?.id === [].concat(matching)[0])
      .map(([k]) => v(...k.split(',').map(Number))),
    inventory: { items: () => [] },
    on: () => {}
  }
  return { bot, cells, state: { home: null, plan: null, unreachableBlocks: new Set(), unreachableDrops: new Set() } }
}

test('横か上に水・溶岩があるブロックは、水をせき止めている。下に水があるだけなら違う', () => {
  const { bot } = world({ '1,64,0': 'water' })
  assert.equal(holdsBackLiquid(bot, v(0, 64, 0)), true)
  assert.equal(holdsBackLiquid(bot, v(4, 64, 0)), false)
  assert.equal(holdsBackLiquid(world({ '0,63,0': 'water' }).bot, v(0, 64, 0)), false) // 下の水は掘っても出てこない
  assert.equal(holdsBackLiquid(world({ '0,65,0': 'lava' }).bot, v(0, 64, 0)), true) // 上
})

test('掘れない理由が出るのは固いブロックだけ（草花・空気・水そのものは水を止めていない）', () => {
  const { bot } = world({ '1,64,0': 'water', '0,64,0': 'oak_log', '1,65,0': 'short_grass' })
  assert.match(liquidReason(bot, v(0, 64, 0), bot.blockAt(v(0, 64, 0))), /holds back water or lava/)
  assert.equal(liquidReason(bot, v(1, 65, 0), bot.blockAt(v(1, 65, 0))), null) // 草
  assert.equal(liquidReason(bot, v(1, 64, 0), bot.blockAt(v(1, 64, 0))), null) // 水そのもの
})

test('木の幹は葉につながっている。葉のない原木の塊（村の畑の縁、柱）は人工物', () => {
  forgetTrees()
  const tree = world({ '5,64,0': 'oak_log', '5,65,0': 'oak_log', '5,66,0': 'oak_log', '5,67,0': 'oak_leaves', '6,66,0': 'oak_leaves' })
  assert.equal(isTreeLog(tree.bot, v(5, 64, 0)), true) // 根元の原木も、つながった塊のどれかが葉の近くにある
  forgetTrees()
  const edge = world({ '9,64,3': 'oak_log', '9,64,4': 'oak_log' })
  assert.equal(isTreeLog(edge.bot, v(9, 64, 3)), false)
  forgetTrees()
  const stripped = world({ '2,64,2': 'stripped_oak_log', '2,65,2': 'oak_leaves' })
  assert.equal(gatherableLog(stripped.bot, stripped.state, v(2, 64, 2)), false) // 皮をはいだ原木は自然にはない
})

test('読み込まれていない所に接した塊は、木として扱う（分からないものを人工物にしない）', () => {
  forgetTrees()
  const { bot } = world({ '5,64,0': 'oak_log' })
  const at = bot.blockAt
  bot.blockAt = (p) => (p.x === 6 ? null : at(p))
  assert.equal(isTreeLog(bot, v(5, 64, 0)), true)
})

test('ボットが置いた原木は、木でなくても集めてよい。別の物に変わっていれば使わない', () => {
  forgetTrees()
  const { bot, state, cells } = world({ '3,64,3': 'oak_log' })
  assert.equal(gatherableLog(bot, state, v(3, 64, 3)), false)
  recordPlaced(state, v(3, 64, 3), 'oak_log', 5)
  forgetTrees()
  assert.equal(gatherableLog(bot, state, v(3, 64, 3)), true)
  cells.set('3,64,3', 'cobblestone')
  assert.equal(placedByMe(bot, state, v(3, 64, 3)), false)
})

test('自動の採集は、木の幹だけを候補にする。水をせき止めている原木は木でも出さない', () => {
  forgetTrees()
  const { bot, state } = world({
    '4,64,0': 'oak_log', '4,65,0': 'oak_leaves', // 木
    '-4,64,0': 'oak_log', // 畑の縁のような、葉のない原木
    '4,64,6': 'oak_log', '4,65,6': 'oak_leaves', '5,64,6': 'water' // 池のふちの木
  })
  assert.deepEqual(digTargets(bot, state, 'oak_log').map((p) => `${p.x},${p.y},${p.z}`), ['4,64,0'])
  recordPlaced(state, v(-4, 64, 0), 'oak_log', 1) // 自分で置いた
  forgetTrees()
  assert.deepEqual(digTargets(bot, state, 'oak_log').map((p) => `${p.x},${p.y},${p.z}`), ['4,64,0', '-4,64,0'])
})

test('目の前の一覧: 木でない原木は数だけで座標は出さず、木があればそちらが一番近い', () => {
  const { bot } = world({ '2,64,0': 'oak_log', '6,64,0': 'oak_log', '6,65,0': 'oak_leaves' })
  const notTree = (p, b) => b.name === 'oak_log' && p.x === 2
  const log = aroundMe(bot, () => [], () => false, notTree).blocks.find((b) => b.name === 'oak_log')
  assert.equal(log.count, 1)
  assert.equal(log.not_trees, 1)
  assert.equal(log.nearest.x, 6)
  const only = aroundMe(world({ '2,64,0': 'oak_log' }).bot, () => [], () => false, () => true).blocks.find((b) => b.name === 'oak_log')
  assert.equal(only.nearest, null)
})

test('掘った跡に水が流れ込むと、埋める対象になる。埋めた・2 回行った・10 分たったものは対象にしない', () => {
  const { bot, state, cells } = world({ '1,64,0': 'water' })
  const now = Number(bot.time.age)
  recordDug(state, v(0, 64, 0), 'oak_log', now - 100)
  assert.equal(leakingHoles(bot, state, now).length, 1) // 空気の跡の横に水
  cells.set('0,64,0', 'water') // もう流れ込んだ
  assert.equal(leakingHoles(bot, state, now).length, 1)
  cells.set('0,64,0', 'oak_log') // 別の物で埋まった
  assert.equal(leakingHoles(bot, state, now).length, 0)
  cells.set('0,64,0', 'air')
  state.dug[0].tries = 2
  assert.equal(leakingHoles(bot, state, now).length, 0)
  state.dug[0].tries = 0
  assert.equal(leakingHoles(bot, state, now + DUG_TICKS + 1).length, 0)
  const dry = world()
  recordDug(dry.state, v(0, 64, 0), 'oak_log', 0)
  assert.equal(leakingHoles(dry.bot, dry.state, 10).length, 0) // 水が近くにない
})

test('bot.dig と blockPlaced を記録する（掘った物の名前は掘る前に取る）', async () => {
  const { bot, state } = world()
  const handlers = {}
  bot.on = (ev, fn) => { handlers[ev] = fn }
  bot.dig = async () => {}
  installLedger(bot, state)
  await bot.dig({ name: 'oak_log', position: v(3, 64, 3), boundingBox: 'block' })
  await bot.dig({ name: 'short_grass', position: v(4, 64, 3), boundingBox: 'empty' })
  assert.deepEqual(state.dug.map((d) => d.name), ['oak_log']) // 草花は水を止めていないので記録しない
  handlers.blockPlaced(null, { name: 'oak_log', position: v(5, 64, 5) })
  handlers.blockPlaced(null, { name: 'dirt', position: v(6, 64, 5) }) // 足場の土などは入れない（原木の記録が押し出される）
  assert.deepEqual(Object.keys(state.placed), ['5,64,5'])
})

test('埋める候補は水が入り込んだ跡だけに出て、id は位置と掘った物だけ。何も持っていなければ出ない', () => {
  const { bot, state } = world({ '1,64,0': 'water' })
  recordDug(state, v(0, 64, 0), 'oak_log', Number(bot.time.age))
  const k = new Knowledge(md)
  const offer = (items) => {
    bot.inventory = { items: () => items }
    return ground(bot, { ...state, unreachableDrops: new Set(), memory: { deaths: [], places: {}, explored: {}, chests: {}, furnaces: {} } }, k,
      { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.filter((c) => c.verb === 'plug_hole')
  }
  assert.equal(offer([]).length, 0)
  const [own] = offer([{ name: 'oak_log', count: 2 }, { name: 'dirt', count: 5 }])
  assert.equal(own.id, 'plug the hole at 0,64,0 where oak_log was dug: water runs into it')
  assert.equal(own.item, 'oak_log') // 同じ物があれば同じ物
  assert.equal(offer([{ name: 'dirt', count: 5 }])[0].item, 'dirt')
})

test('埋める: 行った回数を数え、置けたら埋めた印を付ける', async () => {
  const { bot, state, cells } = world({ '1,64,0': 'water' })
  recordDug(state, v(0, 64, 0), 'oak_log', 1)
  bot.world = { getBlock: bot.blockAt }
  bot.waitForTicks = async () => {}
  bot.equip = async () => {}
  bot.inventory = { items: () => [{ name: 'dirt', count: 3 }] }
  bot.pathfinder = { goto: async () => {}, setGoal: () => {} }
  bot.placeBlock = async (against, face) => { cells.set(`${against.position.x + face.x},${against.position.y + face.y},${against.position.z + face.z}`, 'dirt') }
  const signal = new AbortController().signal
  const result = await PRIMITIVES.plug_hole(bot, state, { item: 'dirt', pos: v(0, 64, 0) }, signal)
  assert.match(result, /plugged the hole at 0,64,0/)
  assert.equal(state.dug[0].plugged, true)
  assert.equal(state.dug[0].tries, 1)
})

test('畑の水のマス（水を入れるために掘った穴）は、水が入っていても埋めない', () => {
  const { bot, state } = world({ '0,64,0': 'water' })
  recordDug(state, v(0, 64, 0), 'grass_block', Number(bot.time.age))
  const water = { x: 0, y: 64, z: 0 }
  state.facilities = { farm1: { kind: 'farm', layout: { crop: 'wheat', water: [water] } } }
  const k = new Knowledge(md)
  const offered = () => ground(bot, { ...state, unreachableDrops: new Set(), memory: { deaths: [], places: {}, explored: {}, chests: {}, furnaces: {} } }, k,
    { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.filter((c) => c.verb === 'plug_hole')
  bot.inventory = { items: () => [{ name: 'dirt', count: 3 }] }
  assert.equal(offered().length, 0)
  state.facilities.farm1.layout.water = [] // 水のマスでなければ、同じ跡は埋める
  assert.equal(offered().length, 1)
})

test('取り残し: 葉がなく何にも触れていない原木の塊（浮いた幹）は集めてよい。地面や板材に触れる塊は集めない', () => {
  const logs = (x, ys) => Object.fromEntries(ys.map((y) => [`${x},${y},0`, 'oak_log']))
  // 下の 3 本が切られて浮いた幹（y 67〜69）、葉は散っている
  const floating = world(logs(5, [67, 68, 69]))
  forgetTrees()
  assert.equal(isTreeLog(floating.bot, v(5, 68, 0)), false)
  assert.equal(isLeftoverLog(floating.bot, v(5, 68, 0)), true)
  assert.equal(gatherableLog(floating.bot, floating.state, v(5, 68, 0)), true)
  // 地面に立つ葉のない柱（村の畑の縁、家の柱）
  const pillar = world(logs(5, [64, 65, 66]))
  forgetTrees()
  assert.equal(gatherableLog(pillar.bot, pillar.state, v(5, 65, 0)), false)
  // 高い所でも板材に触れていれば人工物（家の梁）
  const beam = world({ ...logs(5, [70, 71]), '6,70,0': 'oak_planks' })
  forgetTrees()
  assert.equal(gatherableLog(beam.bot, beam.state, v(5, 70, 0)), false)
  // 皮をはいだ原木に触れていれば人工物
  const stripped = world({ ...logs(5, [70, 71]), '5,72,0': 'stripped_oak_log' })
  forgetTrees()
  assert.equal(gatherableLog(stripped.bot, stripped.state, v(5, 70, 0)), false)
  // 蔓や草に触れていても、取り残し
  const vines = world({ ...logs(5, [67, 68]), '6,67,0': 'vine' })
  forgetTrees()
  assert.equal(isLeftoverLog(vines.bot, v(5, 67, 0)), true)
})

test('取り残し: 読み込まれていない所に接する塊は、木として扱う（取り残しとは言わない）', () => {
  const { bot } = world({ '5,67,0': 'oak_log', '5,68,0': 'oak_log' })
  const at = bot.blockAt
  bot.blockAt = (p) => (p.x === 6 && p.y === 68 ? null : at(p))
  forgetTrees()
  assert.equal(isTreeLog(bot, v(5, 67, 0)), true)
  assert.equal(isLeftoverLog(bot, v(5, 67, 0)), false)
})

test('取り残しの浮いた幹が候補に出る（葉が散っていても）', () => {
  const { bot, state } = world({ '2,67,0': 'oak_log', '2,68,0': 'oak_log' })
  bot.findBlocks = () => [v(2, 67, 0), v(2, 68, 0)]
  forgetTrees()
  assert.deepEqual(digTargets(bot, state, 'oak_log'), [v(2, 67, 0), v(2, 68, 0)])
})

test('土を積んで届く高さ: 土がなければ 5、あれば 4 + 積める数（最大 16）。逆に、その高さに届くのに要る土の数', () => {
  assert.deepEqual([0, 1, 2, 3, 6, 12, 64].map(reachableLogHeight), [5, 5, 6, 7, 10, 16, 16])
  assert.deepEqual([3, 5, 6, 7, 10, 16].map(dirtForLogHeight), [0, 0, 2, 3, 6, 12])
  for (const h of [6, 9, 12, 16]) assert.equal(reachableLogHeight(dirtForLogHeight(h)) >= h, true, `${h} 段上は、要る土で届く`)
})

test('高い幹は、積める土の数までの高さの原木だけ候補になる', () => {
  // 地面は y 63 まで。浮いた幹は y 70〜80（地面から 7〜17 段上）
  const ys = [70, 72, 74, 76, 78, 80]
  const { bot, state } = world(Object.fromEntries(ys.map((y) => [`2,${y},0`, 'oak_log'])))
  bot.findBlocks = ({ matching }) => ([].concat(matching).includes(md.blocksByName.oak_log.id) ? ys.map((y) => v(2, y, 0)) : []) // 近くに掘れる土はない
  const withDirt = (n) => { bot.inventory = { items: () => (n ? [{ name: 'dirt', count: n }] : []) }; forgetTrees(); return digTargets(bot, state, 'oak_log', 10).map((p) => p.y).sort((a, b) => a - b) }
  assert.deepEqual(withDirt(0), [])
  assert.deepEqual(withDirt(1), []) // 塔 1 つでは 5 段上のまま
  assert.deepEqual(withDirt(3), [70]) // 7 段上まで
  assert.deepEqual(withDirt(6), [70, 72]) // 10 段上まで
  assert.deepEqual(withDirt(64), [70, 72, 74, 76, 78]) // 上限は 16 段上。80 は 17 段上
})

test('土を持っていなくても、近くに掘れる土があれば、高い幹は候補になる（dig が先に集める）。掘れる土の数までの高さ', () => {
  const ys = [70, 74, 78]
  const dirtAt = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [`${6 + i},63,0`, 'dirt']))
  const trunk = Object.fromEntries(ys.map((y) => [`2,${y},0`, 'oak_log']))
  const targets = (n) => {
    const { bot, state } = world({ ...trunk, ...dirtAt(n) })
    const cells = Object.fromEntries(Object.entries({ ...trunk, ...dirtAt(n) }))
    bot.findBlocks = ({ matching }) => Object.entries(cells).filter(([, name]) => [].concat(matching).includes(md.blocksByName[name]?.id)).map(([k]) => v(...k.split(',').map(Number)))
    forgetTrees()
    return digTargets(bot, state, 'oak_log', 10).map((p) => p.y).sort((a, b) => a - b)
  }
  assert.deepEqual(targets(0), [], '土がどこにもない')
  assert.deepEqual(targets(3), [70], '土 3 つぶんで 7 段上まで')
  assert.deepEqual(targets(8), [70, 74], '土 8 つぶんで 12 段上まで')
})

// 土を持っていないボットが、7 段上（土 3 つで届く）の原木を掘る。dirts: 近くの掘れる土の数
function climber (dirts) {
  const dirt = Object.fromEntries(Array.from({ length: dirts }, (_, i) => [`${6 + i},63,0`, 'dirt']))
  const { bot, cells, state } = world({ '2,70,0': 'oak_log', ...dirt })
  const held = { dirt: 0, oak_log: 0 }
  bot.findBlocks = ({ matching }) => [...cells].filter(([, n]) => [].concat(matching).includes(md.blocksByName[n]?.id)).map(([k]) => v(...k.split(',').map(Number)))
  bot.inventory = { items: () => Object.entries(held).filter(([, count]) => count).map(([name, count]) => ({ name, count })), emptySlotCount: () => 20 }
  const dug = []
  bot.dig = async (block) => {
    dug.push(block.name)
    cells.set(`${block.position.x},${block.position.y},${block.position.z}`, 'air')
    held[block.name] += 1
  }
  bot.equip = async () => {}
  bot.waitForTicks = async () => {}
  bot.pathfinder = { bestHarvestTool: () => null, goto: async () => {}, setGoal: () => {} }
  return { bot, state: { ...state, dug: [] }, dug, held }
}

test('高い原木は、積む土を持っていなければ、先に近くの土を掘って必要な数を集めてから掘る', async () => {
  const { bot, state, dug, held } = climber(5)
  forgetTrees()
  await PRIMITIVES.dig(bot, state, { block: 'oak_log', pos: v(2, 70, 0) }, new AbortController().signal)
  assert.equal(held.dirt, 3, '7 段上に届くのに要る 3 つ。それ以上は掘らない')
  assert.deepEqual(dug, ['dirt', 'dirt', 'dirt', 'oak_log'])
})

test('積む土が近くになければ、何がどれだけ足りないかを言って止まる（原木は掘らない）', async () => {
  const { bot, state, dug } = climber(1)
  forgetTrees()
  await assert.rejects(PRIMITIVES.dig(bot, state, { block: 'oak_log', pos: v(2, 70, 0) }, new AbortController().signal),
    /the log at 2,70,0 is 7 blocks above the ground: reaching it takes 3 dirt and you carry 1; no dirt within reach to dig for the rest/)
  assert.deepEqual(dug, ['dirt'])
})

test('立って届く低い原木では、土を集めない', async () => {
  const { bot, state, dug } = climber(5)
  bot.blockAt = ((blockAt) => (p) => (p.x === 2 && p.y === 66 && p.z === 0 ? { ...blockAt(p), name: 'oak_log', boundingBox: 'block' } : blockAt(p)))(bot.blockAt)
  forgetTrees()
  await PRIMITIVES.dig(bot, state, { block: 'oak_log', pos: v(2, 66, 0) }, new AbortController().signal)
  assert.deepEqual(dug, ['oak_log'])
})

test('積む土は、原木の真下とそのまわりの列からは掘らない（積む足場の地面）', async () => {
  const { bot, state, dug } = climber(0)
  const cells = new Map([['3,63,0', 'dirt'], ['1,63,1', 'dirt'], ['2,63,0', 'dirt']])
  const at = bot.blockAt
  bot.blockAt = (p) => (cells.has(`${p.x},${p.y},${p.z}`) ? { ...at(p), name: cells.get(`${p.x},${p.y},${p.z}`) } : at(p))
  bot.findBlocks = ({ matching }) => (matching.includes?.(md.blocksByName.dirt.id) || [].concat(matching).includes(md.blocksByName.dirt.id) ? [...cells.keys()].map((k) => v(...k.split(',').map(Number))) : [v(2, 70, 0)])
  forgetTrees()
  await assert.rejects(PRIMITIVES.dig(bot, state, { block: 'oak_log', pos: v(2, 70, 0) }, new AbortController().signal), /no dirt within reach/)
  assert.deepEqual(dug, [])
})

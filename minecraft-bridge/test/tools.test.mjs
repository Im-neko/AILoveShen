import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { createTools } from '../src/tools.mjs'
import { lookAround } from '../src/state.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const knowledge = new Knowledge(md)
const DAY = 1000
const NIGHT = 18000

// y 70 の石の上の 5x5 の家（shelter.test.mjs と同じ）: 壁は x 0..4、z 0..4、室内は 1..3、ドアは (2, 70, 4)。
// 前の家は x 20..24 に同じ形で建っていて、中にベッドがある
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }
const former = { door: v(22, 70, 4), inside: v(22, 70, 3), outside: v(22, 70, 5), min: v(21, 70, 1), max: v(23, 70, 3), bed: v(22, 70, 2), breach: [] }

function blockAt (p, extra = {}) {
  const key = `${p.x},${p.y},${p.z}`
  if (extra[key]) return { name: extra[key], boundingBox: 'block', position: p }
  if (p.y < 70) return { name: 'stone', boundingBox: 'block', position: p }
  for (const h of [home, former]) {
    const [x0, z0] = [h.min.x - 1, h.min.z - 1]
    const [lx, lz] = [p.x - x0, p.z - z0]
    if (lx < 0 || lx > 4 || lz < 0 || lz > 4 || p.y > 71) continue
    if (p.x === h.door.x && p.z === h.door.z) return { name: 'oak_door', boundingBox: 'block', position: p, getProperties: () => ({ open: false }) }
    if (lx === 0 || lx === 4 || lz === 0 || lz === 4) return { name: 'oak_planks', boundingBox: 'block', position: p }
    if (h.bed && p.equals(h.bed)) return { name: 'white_bed', boundingBox: 'block', position: p }
  }
  return { name: 'air', boundingBox: 'empty', position: p }
}

function setup ({ time = DAY, at = v(2.5, 70, 2.5), items = [], mobs = [], extra = {}, candidates = [], check = null, health = 20 } = {}) {
  const entity = { position: at, eyeHeight: 1.62 }
  const entities = { 0: entity }
  mobs.forEach((m, i) => { entities[i + 1] = { id: i + 1, type: 'hostile', height: 1.99, ...m } })
  const bot = {
    entity,
    entities,
    registry: md,
    time: { timeOfDay: time },
    health,
    food: 20,
    blockAt: (p) => blockAt(p, extra),
    findBlock: () => null,
    findBlocks: () => [],
    world: { raycast: () => null },
    inventory: { items: () => items.map(([name, count]) => ({ name, count })) }
  }
  const state = { home, formerHomes: [former], plan: null, busy: false, reflex: false, memory: {}, unreachableDrops: new Set() }
  const runs = []
  const checks = []
  const callTool = createTools({
    bot,
    state,
    knowledge,
    run: async (c, label) => { runs.push({ c, label }); return { ok: true, result: 'done', seconds: 0 } },
    candidates: () => candidates,
    check: check ?? ((specs) => { checks.push(specs); return specs.map(() => ({ met: false, lines: ['have 1 stick (0/1)'], impossible: [] })) })
  })
  return { callTool, runs, checks, bot, state }
}

test('ドアの前にクリーパーがいれば、危なさの数字に関わらず外に出る道具は断る（ドアのそばで爆発する）', async () => {
  const { callTool, runs } = setup({ mobs: [{ name: 'creeper', position: v(2.5, 70, 6.5) }] })
  const r = await callTool('goto', { x: 10, y: 70, z: 10 })
  assert.equal(r.refused, true)
  assert.match(r.result, /a creeper is at the door/)
  assert.equal(runs.length, 0)
})

test('座標が欠けた goto は、どれが欠けたかを言って断る（z を落とした手が同じまま続いた）', async () => {
  const { callTool, runs } = setup()
  const noZ = await callTool('goto', { x: -98, y: 65 })
  assert.equal(noZ.refused, true)
  assert.match(noZ.result, /x, y and z must be numbers: z is missing or not a number \(got x=-98, y=65, z=none\)/)
  assert.match((await callTool('goto', { x: 'a', z: 3 })).result, /x and y are missing or not a number \(got x=a, y=none, z=3\)/)
  assert.equal(runs.length, 0)
})

// 値打ちのある持ち物（ダイヤのツルハシ 15）を持って体力が低いと、外に出る危なさが極めて高い（risk.mjs）
const VALUABLE = [['diamond_pickaxe', 1]]

test('夜でも、危なさが低ければ（体力も満タンで失うものがない）外に出る道具は断らない', async () => {
  const { callTool, runs } = setup({ time: NIGHT })
  const r = await callTool('goto', { x: 10, y: 70, z: 10 })
  assert.equal(r.ok, true, r.result)
  assert.equal(runs.length, 1)
})

test('外に出る危なさが極めて高ければ、理由（risk）をつけて断り、実行しない。家の中での道具は使える', async () => {
  const { callTool, runs } = setup({ time: NIGHT, health: 4, items: VALUABLE })
  const r = await callTool('goto', { x: 10, y: 70, z: 10 })
  assert.equal(r.refused, true)
  assert.match(r.result, /staying inside: too dangerous to go out \(risk extreme, do first: stash valuables in the chest, eat or rest; because health 4\/20, night/)
  // LLM に渡る道具の結果は 300 字で切られる: 先にすることまで届く長さにする
  assert.ok(r.result.length <= 300, `${r.result.length}: ${r.result}`)
  assert.equal(runs.length, 0)
  assert.equal((await callTool('wait')).ok, true)
  assert.equal(runs[0].c.inside, true)
})

test('昼にドアの前に敵がいても、元気で失うものがなければ外に出られる。危なさが極めて高いときだけ断り、敵と戦うことはできる', async () => {
  const skeleton = { name: 'skeleton', position: v(2.5, 70, 6.5) }
  const calm = setup({ mobs: [skeleton] })
  assert.equal((await calm.callTool('goto', { x: 10, y: 70, z: 10 })).ok, true)
  const { callTool, runs } = setup({ mobs: [skeleton], health: 4, items: VALUABLE })
  assert.match((await callTool('goto', { x: 10, y: 70, z: 10 })).result, /too dangerous to go out \(risk extreme/)
  const r = await callTool('attack', { entity: 1 })
  assert.equal(r.ok, true)
  assert.equal(runs[0].c.confront, true)
})

test('override: true は、保護された所（家のドアの内側）へ承知で置く。台帳に断られた所へも、保護を承知なら再び試せる', async () => {
  const { callTool, runs } = setup({ items: [['crafting_table', 1], ['dirt', 4]] })
  const refused = await callTool('place', { item: 'crafting_table', x: 2, y: 70, z: 3 })
  assert.match(refused.result, /current home.*override: true places it anyway/)
  assert.equal(runs.length, 0)
  const again = await callTool('place', { item: 'crafting_table', x: 2, y: 70, z: 3, override: true })
  assert.equal(again.ok, true, again.result)
  assert.equal(runs.at(-1).c.verb, 'place_at')
  // override でも、立っている場所・壁そのものなどの物理的な断りは残る
  assert.match((await callTool('place', { item: 'dirt', x: 0, y: 70, z: 2, override: true })).result, /taken by oak_planks/)
})

test('今の家は掘らないが、前の家（置いてきたベッド）は名指しすれば掘れる（town4d）', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5) })
  assert.match((await callTool('dig', { x: 0, y: 70, z: 2 })).result, /part of the current home/)
  const r = await callTool('dig', { x: 22, y: 70, z: 2 })
  assert.equal(r.ok, true)
  assert.deepEqual([runs[0].c.verb, runs[0].c.block], ['dig', 'white_bed'])
})

test('今の家でも、壊すと決めたとき（demolish）は名指しで掘れる', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5) })
  const refused = await callTool('dig', { x: 0, y: 70, z: 2 })
  assert.match(refused.result, /demolish: true/)
  assert.equal(runs.length, 0)
  const r = await callTool('dig', { x: 0, y: 70, z: 2, demolish: true })
  assert.equal(r.ok, true, r.result)
  assert.equal(runs[0].c.verb, 'dig')
})

test('掘る道具はブロックの名前を世界から決め、道具がなくて何も落ちないものは断る', async () => {
  const ore = { '10,69,10': 'coal_ore' }
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), extra: ore })
  assert.match((await callTool('dig', { x: 10, y: 69, z: 10 })).result, /coal_ore drops nothing without one of: .*pickaxe/)
  assert.match((await callTool('dig', { x: 10, y: 71, z: 10 })).result, /there is air/)
  // 何もない座標には、そのまわりの固いブロックと座標を添える（座標が 1 ずれただけのことが多い）
  assert.match((await callTool('dig', { x: 10, y: 71, z: 10 })).result, /Solid blocks near there: .* at \d+,\d+,\d+/)
  const { callTool: withPick } = setup({ at: v(10.5, 70, 10.5), items: [['wooden_pickaxe', 1]], extra: ore })
  assert.equal((await withPick('dig', { x: 10, y: 69, z: 10 })).ok, true)
  assert.equal(runs.length, 0)
})

test('家があるとき、地表に近い石は掘らない（採掘場から）。家がなければ掘れる', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), items: [['wooden_pickaxe', 1]] })
  const r = await callTool('dig', { x: 10, y: 69, z: 10 })
  assert.equal(r.refused, true)
  assert.match(r.result, /close to the surface.*mine/)
  assert.equal(runs.length, 0)
  const { callTool: noHome, state } = setup({ at: v(10.5, 70, 10.5), items: [['wooden_pickaxe', 1]] })
  state.home = null
  assert.equal((await noHome('dig', { x: 10, y: 69, z: 10 })).ok, true)
})

test('洞窟の鉱石は、道具の dig でも掘らない（家の床より 10 以上下。浅い露頭は掘れる）', async () => {
  const extra = { '10,49,10': 'iron_ore', '10,69,10': 'iron_ore' }
  const { callTool, runs } = setup({ at: v(10.5, 50, 10.5), items: [['stone_pickaxe', 1]], extra })
  const r = await callTool('dig', { x: 10, y: 49, z: 10 })
  assert.equal(r.refused, true)
  assert.match(r.result, /will not dig iron_ore.*never in a cave/)
  assert.equal(runs.length, 0)
  const { callTool: outcrop } = setup({ at: v(10.5, 70, 10.5), items: [['stone_pickaxe', 1]], extra })
  assert.equal((await outcrop('dig', { x: 10, y: 69, z: 10 })).ok, true) // 浅い露頭
})

test('find_blocks は、洞窟の深い鉱石を出さない（dig が断る）。全部がそうなら、理由を言う。浅い露頭は出す', async () => {
  const { callTool, bot } = setup({ at: v(10.5, 50, 10.5), items: [['stone_pickaxe', 1]], extra: { '10,49,10': 'iron_ore', '10,69,10': 'iron_ore' } })
  bot.findBlocks = () => [v(10, 49, 10)]
  const deep = (await callTool('find_blocks', { block: 'iron_ore', radius: 32 })).result
  assert.match(deep, /no iron_ore you may take.*1 lie deep in caves.*never in a cave/)
  bot.findBlocks = () => [v(10, 49, 10), v(10, 69, 10)]
  assert.deepEqual((await callTool('find_blocks', { block: 'iron_ore', radius: 32 })).result.map((f) => f.y), [69])
})

test('goto は水の上の座標へ歩かない（海を歩いて溺れた）。陸の座標と、ボートのときは通す', async () => {
  const extra = { '8,71,8': 'water', '8,70,8': 'water', '8,69,8': 'water', '8,68,8': 'water' }
  const { callTool, runs } = setup({ at: v(2.5, 70, 2.5), extra })
  const r = await callTool('goto', { x: 8, y: 71, z: 8 })
  assert.equal(r.refused, true)
  assert.match(r.result, /over water.*drown/)
  assert.equal(runs.length, 0)
  assert.equal((await callTool('goto', { x: 6, y: 70, z: 6 })).ok, true) // 陸
})

test('育ち途中の作物は掘らない（種しか出ず、畑が減る）', async () => {
  const { callTool } = setup({ at: v(10.5, 70, 10.5), extra: { '11,70,10': 'wheat' } })
  assert.match((await callTool('dig', { x: 11, y: 70, z: 10 })).result, /wheat at 11,70,10 is not ripe yet \(age 0\/7\)/)
})

test('耕地は掘らない（作物の下の座標で呼ばれたら、作物の場所を教えて断る）。壊すと決めたときだけ掘る', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), extra: { '11,69,10': 'farmland', '11,70,10': 'wheat' } })
  const refused = await callTool('dig', { x: 11, y: 69, z: 10 })
  assert.match(refused.result, /will not dig farmland at 11,69,10.*the wheat on it is at 11,70,10/)
  assert.equal(runs.length, 0)
  assert.equal((await callTool('dig', { x: 11, y: 69, z: 10, demolish: true })).ok, true)
})

test('置く道具は空いたセルに、隣のブロックの面を借りて置く。自分が立っている所には置かない', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), items: [['dirt', 4]] })
  assert.match((await callTool('place', { item: 'dirt', x: 10, y: 70, z: 10 })).result, /you are standing in/)
  assert.match((await callTool('place', { item: 'dirt', x: 10, y: 69, z: 10 })).result, /taken by stone/)
  assert.match((await callTool('place', { item: 'cobblestone', x: 11, y: 70, z: 10 })).result, /you have no cobblestone/)
  assert.equal((await callTool('place', { item: 'dirt', x: 11, y: 70, z: 10 })).ok, true)
  assert.deepEqual(runs[0].c.against, v(11, 69, 10))
})

test('クラフトはレシピから作業台が要るかを決め、近くに作業台がなければ断る', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), items: [['oak_planks', 4]] })
  assert.match((await callTool('craft', { item: 'wooden_pickaxe' })).result, /needs a crafting table within reach/)
  assert.equal((await callTool('craft', { item: 'stick', times: 2 })).ok, true)
  assert.deepEqual([runs[0].c.needsTable, runs[0].c.times], [false, 2])
  assert.match((await callTool('craft', { item: 'dirt' })).result, /has no crafting recipe/)
})

test('作業台を持っているのに近くになければ、断りは「置く」ことを教える（作ったあと置かずに 4 回断られた）', async () => {
  const { callTool } = setup({ at: v(10.5, 70, 10.5), items: [['crafting_table', 1], ['oak_planks', 4]] })
  const refused = (await callTool('craft', { item: 'wooden_pickaxe' })).result
  assert.match(refused, /you carry 1 crafting_table: place it/)
  assert.doesNotMatch(refused, /craft one/)
})

test('ソルバーの提案は、今ある候補の id だけ実行する', async () => {
  const suggestion = { id: 'dig oak_log at 5,70,5', verb: 'dig', pos: v(5, 70, 5), block: 'oak_log' }
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), candidates: [suggestion] })
  assert.match((await callTool('do_suggestion', { id: 'dig oak_log at 9,70,9' })).result, /not a current suggestion/)
  assert.equal((await callTool('do_suggestion', { id: suggestion.id })).ok, true)
  assert.equal(runs[0].label, suggestion.id)
})

test('数字だけが違う提案は同じ手として実行し、違うものは今の提案を添えて断る', async () => {
  const bed = { id: 'craft white_bed x1（7.5m）', verb: 'craft', item: 'white_bed' }
  const logs = [
    { id: 'dig oak_log at 5,70,5', verb: 'dig', pos: v(5, 70, 5), block: 'oak_log' },
    { id: 'dig oak_log at 6,70,5', verb: 'dig', pos: v(6, 70, 5), block: 'oak_log' }
  ]
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), candidates: [bed, ...logs] })
  const same = await callTool('do_suggestion', { id: 'craft white_bed x1（7.7m）' })
  assert.equal(same.ok, true)
  assert.equal(runs[0].label, bed.id)
  // 数字を除いても複数に当たるなら、選べない。同じ動詞の提案を先に添える
  const many = await callTool('do_suggestion', { id: 'dig oak_log at 9,70,9' })
  assert.match(many.result, /not a current suggestion \(now: dig oak_log at 5,70,5; dig oak_log at 6,70,5; craft white_bed/)
  assert.equal(runs.length, 1)
})

test('提案の id の空白を _ で書いても、同じ手として実行する（explore_east は explore east）', async () => {
  const east = { id: 'explore east', verb: 'explore', dir: 'east' }
  const north = { id: 'explore north', verb: 'explore', dir: 'north' }
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), candidates: [east, north] })
  const done = await callTool('do_suggestion', { id: 'explore_east' })
  assert.equal(done.ok, true)
  assert.equal(runs[0].label, 'explore east')
  // 当たるものがなければ、これまでどおり今の提案を添えて断る
  assert.match((await callTool('do_suggestion', { id: 'explore_west' })).result, /not a current suggestion \(now: explore east; explore north\)/)
})

test('boat のような種類の名前は、持っている板材で作れる品物にして作る。作れなければ品物の名前を教える', async () => {
  const { callTool } = setup({ items: [['oak_planks', 10]] })
  // 品物にできた（作業台がなければ、oak_boat として断られる）
  assert.match((await callTool('craft', { item: 'boat' })).result, /oak_boat needs a crafting table/)
  const { callTool: none } = setup({ items: [['dirt', 1]] })
  assert.match((await none('craft', { item: 'boat' })).result, /boat is not an item name; use one of oak_boat/)
})

test('作れる回数が残っていれば、結果に times を添えるための数を渡す（1 回ずつ作り直させない）', async () => {
  const { callTool, runs } = setup({ items: [['oak_planks', 8]] })
  await callTool('craft', { item: 'stick' })
  assert.equal(runs[0].c.more, 3) // 板材 8 枚は 4 回分、1 回作るので残り 3 回
  await callTool('craft', { item: 'stick', times: 4 })
  assert.equal(runs[1].c.more, 0)
})

test('調べものの道具はすぐ返り、行動を実行しない。how_to_get はソルバーの木を返す', async () => {
  const { callTool, runs, checks } = setup()
  const recipe = await callTool('recipe_of', { item: 'stick' })
  assert.ok(recipe.result.crafting.some((r) => r.ingredients.oak_planks === 2 || Object.keys(r.ingredients).some((n) => n.endsWith('_planks'))))
  const how = await callTool('how_to_get', { item: 'stick', count: 1 })
  assert.deepEqual(how.result.steps, ['have 1 stick (0/1)'])
  assert.deepEqual(checks[0], [{ predicate: 'have', item: 'stick', count: 1 }])
  assert.equal(runs.length, 0)
})

test('知識と合わない引数（知らないアイテム）は、プレイを止めずに理由を返す', async () => {
  const { callTool } = setup({ check: () => { throw new Error('unknown item or group: not_an_item') } })
  assert.equal((await callTool('smelt', { input: 'not_an_item' })).refused, true)
  const how = await callTool('how_to_get', { item: 'not_an_item' })
  assert.deepEqual([how.refused, how.result], [true, 'refused: unknown item or group: not_an_item'])
})

test('実行中と反射の間は、道具を断る', async () => {
  const { callTool, state } = setup()
  state.busy = true
  assert.match((await callTool('wait')).result, /another action is running/)
  state.busy = false
  state.reflex = true
  assert.match((await callTool('wait')).result, /reflex/)
})

test('まわりの形: 3 段の縦穴の底では、隣がすべて歩いて上がれない壁になる（town4c）', () => {
  // (10, 67..69, 10) の 1 マスを掘った縦穴。まわりは y 69 まで石、その上は空気。東に水たまり
  const pit = (p) => {
    if (p.x === 12 && p.z === 10 && p.y === 69) return { name: 'water', boundingBox: 'empty' }
    if (p.y >= 70) return { name: 'air', boundingBox: 'empty' }
    if (p.x === 10 && p.z === 10 && p.y >= 67) return { name: 'air', boundingBox: 'empty' }
    return { name: 'stone', boundingBox: 'block' }
  }
  const bot = { entity: { position: v(10.5, 67, 10.5) }, blockAt: pit }
  const view = lookAround(bot, 2)
  assert.equal(view.walls_around, 8)
  assert.deepEqual(view.grid, [
    '+3 +3 +3 +3 +3',
    '+3 +3 +3 +3 +3',
    '+3 +3  @ +3  ~',
    '+3 +3 +3 +3 +3',
    '+3 +3 +3 +3 +3'
  ])
  assert.equal(view.head_blocked, false)
  assert.equal(view.standing_on, 'stone')
})

// 高さの格子は、足元の高さから一番近い立てる所で測る。上から見下ろすと、天井・屋根・葉を地面と読む
const air = { name: 'air', boundingBox: 'empty' }
const stone = { name: 'stone', boundingBox: 'block' }

test('まわりの形: 岩の天井のある高さ 2 の東西のトンネルでは、前後は同じ高さ、横は壁（洞窟）', () => {
  // y 60..61 が空いた東西のトンネル（z = 10 の 1 列）、ほかは岩
  const tunnel = (p) => (p.z === 10 && (p.y === 60 || p.y === 61)) ? air : stone
  const view = lookAround({ entity: { position: v(10.5, 60, 10.5) }, blockAt: tunnel }, 1)
  assert.deepEqual(view.grid, [
    ' #  #  #',
    ' 0  @  0',
    ' #  #  #'
  ])
  assert.equal(view.walls_around, 6)
  assert.equal(view.head_blocked, true)
})

test('まわりの形: 家の中では、床は同じ高さ、壁は屋根の上（屋根を地面と読まない）', () => {
  // 床 y 70、壁 x/z = 0 と 4（y 70..72）、屋根 y 73（x/z 0..4）、ボットは (2, 70, 2)
  const house = (p) => {
    if (p.y < 70) return stone
    const inFootprint = p.x >= 0 && p.x <= 4 && p.z >= 0 && p.z <= 4
    if (inFootprint && p.y === 73) return { name: 'oak_planks', boundingBox: 'block' }
    const wall = inFootprint && (p.x === 0 || p.x === 4 || p.z === 0 || p.z === 4)
    return wall && p.y <= 72 ? { name: 'oak_planks', boundingBox: 'block' } : air
  }
  const view = lookAround({ entity: { position: v(2.5, 70, 2.5) }, blockAt: house }, 2)
  assert.deepEqual(view.grid[2], '+4  0  @  0 +4')
  assert.deepEqual(view.grid[1], '+4  0  0  0 +4')
  assert.equal(view.walls_around, 0) // 隣は床
})

test('まわりの形: 木の葉の下の平らな地面は、同じ高さ（葉を地面と読まない）', () => {
  const canopy = (p) => {
    if (p.y < 70) return { name: 'grass_block', boundingBox: 'block' }
    if (p.y >= 73 && p.y <= 75) return { name: 'oak_leaves', boundingBox: 'block' }
    return air
  }
  const view = lookAround({ entity: { position: v(0.5, 70, 0.5) }, blockAt: canopy }, 1)
  assert.deepEqual(view.grid, [' 0  0  0', ' 0  @  0', ' 0  0  0'])
})

test('家の部屋の中には何でも置ける（ドアの内側の通り道とベッドの場所は残す）。松明は壁に付ける', async () => {
  // 家の中（ボットは (2, 70, 2)）、昼、ベッドなし。壁は x 0 / 4、z 0 / 4 の y 70..71
  const { callTool, runs } = setup({ items: [['crafting_table', 1], ['dirt', 4], ['torch', 4]] })
  assert.equal((await callTool('place', { item: 'crafting_table', x: 1, y: 70, z: 1 })).ok, true)
  assert.equal(runs.at(-1).c.verb, 'place_at')
  assert.match((await callTool('place', { item: 'crafting_table', x: 2, y: 70, z: 3 })).result, /current home/) // ドアの内側
  assert.match((await callTool('place', { item: 'torch', x: 2, y: 71, z: 3 })).result, /current home/) // その頭の高さも通り道
  // 松明（2026-09-27: 家の一部として断られ、placed(torch, home) が進まなかった）: 壁に付ける
  assert.equal((await callTool('place', { item: 'torch', x: 1, y: 71, z: 2 })).ok, true)
  assert.deepEqual(runs.at(-1).c.against, v(0, 71, 2))
  // 床に置くこともできる（on: floor）。土も部屋の中なら置ける
  assert.equal((await callTool('place', { item: 'torch', x: 3, y: 70, z: 1, on: 'floor' })).ok, true)
  assert.deepEqual(runs.at(-1).c.against, v(3, 69, 1))
  assert.equal((await callTool('place', { item: 'dirt', x: 1, y: 70, z: 3 })).ok, true)
  // 家の壁そのものには置けない
  assert.match((await callTool('place', { item: 'torch', x: 0, y: 70, z: 2 })).result, /taken by oak_planks/)
  // ベッドの場所が残らない置き方は断る: 西と東の列、真ん中の奥がふさがっている
  const cramped = { '1,70,2': 'chest', '1,70,3': 'chest', '3,70,1': 'chest', '3,70,2': 'chest', '3,70,3': 'chest' }
  const { callTool: tight } = setup({ items: [['crafting_table', 1]], extra: cramped })
  assert.match((await tight('place', { item: 'crafting_table', x: 2, y: 70, z: 1 })).result, /current home/)
})

test('チェストや作業台の面には付けない（置く代わりに開いてしまう）', async () => {
  // (2, 71, 1) の横は空気と、北の壁の代わりのチェストだけ
  const { callTool } = setup({ items: [['torch', 4]], extra: { '2,71,0': 'chest' } })
  const r = await callTool('place', { item: 'torch', x: 2, y: 71, z: 1, on: 'wall' })
  assert.equal(r.refused, true)
  assert.match(r.result, /nothing on the wall side 2,71,1/)
})

test('建築予定地には作業台を仮設できる（土などは今までどおり断る）', async () => {
  const { callTool, state } = setup({ at: v(10.5, 70, 10.5), items: [['crafting_table', 1], ['dirt', 4]] })
  state.plan = { origin: v(8, 70, 8), size: { width: 7, depth: 7, height: 4 } }
  assert.equal((await callTool('place', { item: 'crafting_table', x: 12, y: 70, z: 10 })).ok, true)
  assert.match((await callTool('place', { item: 'dirt', x: 12, y: 70, z: 11 })).result, /house being built/)
})

test('家具を動かす: 前の家のベッドは家に運べる、今の家の家具は家の中でだけ、ベッドの置き先は家、中身のあるチェストは断る', async () => {
  const { callTool, runs, state } = setup({ at: v(10.5, 70, 10.5), extra: { '3,70,1': 'crafting_table', '1,70,3': 'chest' } })
  const bed = await callTool('move_furniture', { x: 22, y: 70, z: 2 })
  assert.equal(bed.ok, true)
  assert.deepEqual([runs[0].c.verb, runs[0].c.block, runs[0].c.to], ['move_placed', 'white_bed', undefined])
  assert.match((await callTool('move_furniture', { x: 22, y: 70, z: 2, to_x: 12, to_y: 70, to_z: 12 })).result, /a bed is always put in the home/)
  // 今の家の作業台: 家の中なら動かせる、外へは出さない
  assert.equal((await callTool('move_furniture', { x: 3, y: 70, z: 1, to_x: 2, to_y: 70, to_z: 2 })).ok, true)
  assert.match((await callTool('move_furniture', { x: 3, y: 70, z: 1, to_x: 12, to_y: 70, to_z: 12 })).result, /stays in the home/)
  state.memory.chests = { '1,70,3': { contents: { oak_log: 5 } } }
  assert.match((await callTool('move_furniture', { x: 1, y: 70, z: 3 })).result, /still holds oak_log 5/)
  assert.match((await callTool('move_furniture', { x: 10, y: 69, z: 10 })).result, /no bed, crafting table, furnace or chest/)
})

test('断られた場所へ同じ手を呼び直すと、実行せずに前の理由と別の手を返す（台帳）', async (t) => {
  process.env.BRIDGE_GUARDS = 'standard' // 標準の断り（設計書 125）
  t.after(() => { delete process.env.BRIDGE_GUARDS })
  const { callTool, runs, state } = setup()
  const first = await callTool('dig', { x: 0, y: 70, z: 2 })
  assert.match(first.result, /will not dig oak_planks at 0,70,2: it is part of the current home/)
  const again = await callTool('dig', { x: 0, y: 70, z: 2 })
  assert.equal(again.refused, true)
  assert.match(again.result, /was tried before and will not work: will not dig oak_planks.*choose another place/)
  assert.equal(runs.length, 0)
  assert.equal(state.refusals.size, 1) // 呼び直しで期限を延ばさない
  assert.equal((await callTool('dig', { x: 0, y: 70, z: 2, demolish: true })).ok, true) // 壊すと決めたなら台帳を通り越す
})

test('find_blocks は、家の壁と断られた場所を出さない', async () => {
  const { callTool, bot, state } = setup()
  bot.findBlocks = () => [v(0, 70, 2), v(30, 70, 30), v(31, 70, 30)]
  const found = (await callTool('find_blocks', { block: 'oak_planks', radius: 64 })).result
  assert.deepEqual(found.map((f) => f.x), [30, 31].sort((a, b) => a - b)) // 家の壁（0,70,2）は出ない
  state.refusals = new Map()
  const { noteRefusal } = await import('../src/refusals.mjs')
  noteRefusal(state, 'dig', v(30, 70, 30), 'refused: nothing to dig')
  assert.deepEqual((await callTool('find_blocks', { block: 'oak_planks', radius: 64 })).result.map((f) => f.x), [31])
})

test('find_blocks は、読み込まれていない所と半径の外を視界の地図から補い、実物が違うものは直して出さない', async () => {
  const { callTool, bot, state } = setup()
  const { WorldMap, makeClassifier, AIR } = await import('../src/worldmap.mjs')
  const cls = makeClassifier(md)
  const map = new WorldMap()
  const log = cls.cellOf('oak_log')
  const leaves = cls.cellOf('oak_leaves')
  for (const [x, z] of [[30, 30], [80, 10]]) {
    map.set(x, 72, z, log, 1)
    map.set(x, 73, z, AIR, 1)
    for (let dx = -1; dx <= 1; dx++) map.set(x + dx, 74, z, leaves, 1)
  }
  state.worldMap = map
  state.mapCells = cls
  const base = bot.blockAt
  bot.blockAt = (p) => (p.x > 70 ? null : base(p)) // x > 70 は読み込まれていない。(30, 72, 30) は読み込まれていて空気
  const found = (await callTool('find_blocks', { block: 'oak_log', radius: 64 })).result
  assert.deepEqual(found.map((f) => [f.x, f.source]), [[80, 'map']])
  assert.equal(map.get(30, 72, 30).cell, AIR, '実物が違うので地図を直した')
  assert.match((await callTool('find_blocks', { block: 'spruce_log', radius: 64 })).result, /no spruce_log within 64m or in the map/)
})

test('クラフトは材料が足りなければ実行せず、何がいくつ足りないか・持ち物で何回作れるかを返す', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), items: [['oak_planks', 3]] })
  // 棒: 板材 2 個から 4 本。3 個では 1 回だけ
  const two = await callTool('craft', { item: 'stick', times: 2 })
  assert.equal(two.refused, true)
  assert.match(two.result, /cannot craft stick x2: needs 4 oak_planks \(2 per craft; you have 3, 1 short\)/)
  assert.match(two.result, /what you carry makes only 1 \(4 stick per craft\)/)
  assert.match(two.result, /Get the oak_planks first \(how_to_get\)/)
  assert.equal((await callTool('craft', { item: 'stick', times: 1 })).ok, true)
  assert.equal(runs.length, 1)
  const none = setup({ at: v(10.5, 70, 10.5), items: [] })
  assert.match((await none.callTool('craft', { item: 'stick' })).result, /what you carry makes none \(one craft gives 4 stick\)/)
})

test('水をせき止めているブロックは、demolish でも掘らない（畑の縁の原木を掘って水が流れ出た）。ふつうのブロックは通る', async () => {
  const { callTool, runs } = setup({ at: v(10.5, 70, 10.5), extra: { '11,70,10': 'oak_log', '12,70,10': 'water', '9,70,10': 'dirt' } })
  const refused = await callTool('dig', { x: 11, y: 70, z: 10 })
  assert.equal(refused.refused, true)
  assert.match(refused.result, /holds back water or lava/)
  assert.doesNotMatch(refused.result, /demolish/) // 壊せば直る、とは言わない
  const forced = await callTool('dig', { x: 11, y: 70, z: 10, demolish: true })
  assert.equal(forced.refused, true)
  assert.equal(runs.length, 0)
  assert.equal((await callTool('dig', { x: 9, y: 70, z: 10 })).ok, true)
})

test('岩に囲まれた所から岩の向こうの遠い所へ goto すると、経路探索の前に断り、掘って出るよう伝える', async () => {
  // 家の外（10.5, 70, 10.5）の周りを、石の地面（y < 70）の下へ 3 マス掘った部屋に見立てる
  const pocket = (p) => p.y >= 66 && p.y <= 68 && Math.abs(p.x - 10) <= 2 && Math.abs(p.z - 10) <= 2
  const { callTool, runs, bot } = setup({ at: v(10.5, 66, 10.5) })
  bot.blockAt = (p) => pocket(p)
    ? { name: 'air', boundingBox: 'empty', position: p }
    : { name: 'stone', boundingBox: 'block', position: p }
  const r = await callTool('goto', { x: 10, y: 66, z: 30 })
  assert.equal(r.refused, true)
  assert.match(r.result, /closed space \(25 cells you can walk in\)/)
  assert.match(r.result, /climb out of the closed space/)
  assert.equal(runs.length, 0)
  // 部屋のすぐ外なら、掘って行ける近さなので通す
  assert.equal((await callTool('goto', { x: 10, y: 66, z: 15 })).ok, true)
})

test('build_next は続けて置く個数を渡す（省くと上限まで。1 ブロックごとに LLM を呼ばない）', async () => {
  const plan = { status: () => ({ complete: false, placed: 0, total: 144 }) }
  const { callTool, runs, state } = setup()
  state.plan = plan
  await callTool('build_next', {})
  await callTool('build_next', { count: 5 })
  assert.deepEqual(runs.map((r) => r.c.count), [60, 5])
})

test('goto の y が高さの範囲を超え、y と z を入れ替えると近いなら、取り違えを指摘する', async () => {
  const { callTool, runs } = setup({ at: v(2.5, 70, -300.5) })
  const r = await callTool('goto', { x: 3, y: -300, z: 70 })
  assert.equal(r.refused, true)
  assert.match(r.result, /did you swap y and z\? 3,70,-300 is/)
  assert.equal(runs.length, 0)
  const far = await callTool('goto', { x: 3, y: -500, z: 900 })
  assert.doesNotMatch(far.result, /swap/)
})

test('家に帰る小目標のときは、家から遠い所へ goto しない（家のドアの座標と go_home を教えて断る）', async () => {
  const at = setup({ at: v(2.5, 70, 2.5) })
  at.state.goal = { spec: { predicate: 'at_home' } }
  at.state.home = { ...home, door: v(8, 70, 8) }
  const r = await at.callTool('goto', { x: 0, y: 0, z: -100 })
  assert.equal(r.refused, true)
  assert.match(r.result, /the goal is at_home and the home's door is at 8,70,8\. Use go_home/)
  assert.equal(at.runs.length, 0)
  assert.equal((await at.callTool('goto', { x: 10, y: 70, z: 10 })).refused, undefined)
  at.state.goal = { spec: { predicate: 'have', item: 'stick', count: 4 } }
  assert.equal((await at.callTool('goto', { x: 0, y: 70, z: -100 })).refused, undefined)
})

test('クリーパーが 16 m 以内にいる間は、go_home と家のそばへの goto を断る（追ってきて、壁を吹き飛ばす）', async () => {
  const creeper = { name: 'creeper', position: v(2.5, 70, 22.5) }
  const t = setup({ at: v(2.5, 70, 12.5), mobs: [creeper] })
  const home1 = await t.callTool('go_home')
  assert.equal(home1.refused, true)
  assert.match(home1.result, /a creeper is within 16 m.*do not lead it to the house/)
  const near = await t.callTool('goto', { x: 2, y: 70, z: 8 })
  assert.equal(near.refused, true)
  assert.match(near.result, /next to the home/)
  // 家から離れる向きの goto は断らない。クリーパーが遠いか、ほかのモブなら go_home も通る
  assert.equal((await t.callTool('goto', { x: 2, y: 70, z: 40 })).refused, undefined)
  const far = setup({ at: v(2.5, 70, 12.5), mobs: [{ ...creeper, position: v(2.5, 70, 40.5) }] })
  assert.equal((await far.callTool('go_home')).refused, undefined)
  const zombie = setup({ at: v(2.5, 70, 12.5), mobs: [{ name: 'zombie', position: v(2.5, 70, 22.5) }] })
  assert.equal((await zombie.callTool('go_home')).refused, undefined)
})

test('食べ物でないものを食べようとしたら、持っている食べ物（なければ、ないこと）を教えて断る', async () => {
  const none = setup({ items: [['wheat_seeds', 3]] })
  const r = await none.callTool('eat', { item: 'wheat_seeds' })
  assert.equal(r.refused, true)
  assert.match(r.result, /wheat_seeds is not food\. You carry no food: get some first/)
  const some = setup({ items: [['wheat_seeds', 3], ['beef', 2], ['rotten_flesh', 4]] })
  const r2 = await some.callTool('eat', { item: 'wheat_seeds' })
  assert.match(r2.result, /Food you carry: beef x2$/)
  assert.equal(some.runs.length, 0)
})

test('松明が足りないと、8 ブロック以上下の地下へは歩いて下りない（数は行き先までの距離から）。足りれば行ける', async () => {
  const { callTool } = setup({ at: v(2.5, 70, 2.5), items: [['stone_pickaxe', 1]] })
  const r = await callTool('goto', { x: 3, y: 50, z: 3 })
  assert.equal(r.refused, true)
  assert.match(r.result, /will not walk down to 3,50,3.*you carry 0 torches \(7 needed/)
  const few = setup({ at: v(2.5, 70, 2.5), items: [['torch', 4]] })
  const r2 = await few.callTool('goto', { x: 3, y: 50, z: 3 })
  assert.equal(r2.refused, true)
  assert.match(r2.result, /you carry 4 torches \(7 needed/)
  const lit = setup({ at: v(2.5, 70, 2.5), items: [['torch', 7]] })
  assert.equal((await lit.callTool('goto', { x: 3, y: 50, z: 3 })).ok, true)
  // 地表で動くだけなら、松明はいらない
  assert.equal((await callTool('goto', { x: 8, y: 70, z: 8 })).ok, true)
})

test('座標のマイナスを落としたら、符号を戻すと届く座標に読み直して実行し、直したことを結果に添える（z=-285 を 285 と書いた）', async () => {
  const { callTool, runs } = setup({ at: v(55.5, 77, -283.5), extra: { '54,77,-285': 'dirt', '54,77,-283': 'dirt' } })
  const r = await callTool('dig', { x: 54, y: 77, z: 285 })
  assert.equal(r.refused, undefined, r.result)
  assert.equal(runs.length, 1)
  assert.deepEqual([runs[0].c.pos.x, runs[0].c.pos.y, runs[0].c.pos.z], [54, 77, -285])
  assert.match(r.result, /^done \(read 54,77,285 as 54,77,-285: coordinates are signed, and 54,77,285 is 56\dm away.*write the minus sign next time\)$/)
  // 次の呼び出しに前の直しを持ち越さない
  assert.equal((await callTool('dig', { x: 54, y: 77, z: -283 })).result, 'done')
  // 調べるも同じ（調べた座標は直した座標）
  const seen = await callTool('inspect', { x: 54, y: 77, z: 285 })
  assert.deepEqual(seen.result.cell, { x: 54, y: 77, z: -285 }, JSON.stringify(seen))
  // 置くも同じ（空いた所へ。土を 1 つ持っている）
  const put = setup({ at: v(55.5, 77, -283.5), items: [['dirt', 1]], extra: { '54,76,-285': 'dirt' } })
  const placed = await put.callTool('place', { item: 'dirt', x: 54, y: 77, z: 285, intent: 'x' })
  assert.equal(placed.refused, undefined, placed.result)
  assert.deepEqual([put.runs[0].c.pos.x, put.runs[0].c.pos.z], [54, -285])
  // 符号を戻しても遠いなら、直さず断る
  const far = await callTool('dig', { x: 900, y: 77, z: 900 })
  assert.equal(far.refused, true)
  assert.doesNotMatch(far.result, /minus sign/)
})

test('マイナスの落ちを直すのは、届く座標がちょうど 1 つのときだけ（どれか分からなければ断って、候補を教える）。z=0 の符号違いは同じ座標', async () => {
  // (-40,-11) は遠い。符号を戻した (40,-11) と (40,11) の両方が届く: どちらか決められない
  const both = setup({ at: v(10.5, 70, 0.5), extra: { '40,70,-11': 'dirt', '40,70,11': 'dirt' } })
  const r = await both.callTool('dig', { x: -40, y: 70, z: -11 })
  assert.equal(r.refused, true)
  assert.match(r.result, /did you drop a minus sign/)
  assert.equal(both.runs.length, 0)
  // z=0 なら、符号を戻した座標は 1 つ
  const one = setup({ at: v(10.5, 70, 0.5), extra: { '30,70,0': 'dirt' } })
  const fixed = await one.callTool('dig', { x: -30, y: 70, z: 0 })
  assert.equal(fixed.refused, undefined, fixed.result)
  assert.equal(one.runs[0].c.pos.x, 30)
})

test('find_blocks に動物の名前を渡したら、ブロックではないと教える（羊を find_blocks で探して断られ続けた）', async () => {
  const { callTool } = setup()
  const animal = await callTool('find_blocks', { block: 'sheep' })
  assert.equal(animal.refused, true)
  assert.match(animal.result, /unknown block sheep: it is an animal or a mob, not a block.*search_memory/)
  const nonsense = await callTool('find_blocks', { block: 'no_such_thing' })
  assert.equal(nonsense.result, 'refused: unknown block no_such_thing')
})

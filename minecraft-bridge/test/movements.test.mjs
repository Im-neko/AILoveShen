import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import vec3Pkg from 'vec3'
import { configureMovements } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

test('まだ読み込まれていないチャンクに届く経路でも例外にならない（代わりのブロックは位置を持たない）', () => {
  let m
  const bot = { registry: minecraftData('1.21.4'), entities: {}, entity: { position: new Vec3(0, 70, 0) }, blockAt: () => null, pathfinder: { setMovements: (set) => { m = set } } }
  const state = { plan: { origin: new Vec3(10, 70, 10), size: { width: 5, depth: 5 } } }
  configureMovements(bot, state)
  const standIn = m.getBlock(new Vec3(500, 70, 500), 0, 0, 0)
  assert.equal(standIn.position, undefined)
  assert.equal(m.exclusionStep(standIn), 0)
})

test('歩きながら自然の地形を掘り、土を積んで登れる。家と置いた物は壊さず、家に足場を置かない（town4c: 洞窟の穴から出られなかった）', () => {
  let m
  const bot = { registry: minecraftData('1.21.4'), entities: {}, entity: { position: new Vec3(0, 70, 0) }, blockAt: () => null, pathfinder: { setMovements: (set) => { m = set } } }
  const home = { min: new Vec3(20, 70, 20), max: new Vec3(22, 70, 22) }
  const state = { home, formerHomes: [], plan: null }
  configureMovements(bot, state)
  const at = (name, x, y, z) => ({ name, position: new Vec3(x, y, z) })
  for (const name of ['stone', 'dirt', 'grass_block', 'gravel', 'deepslate', 'iron_ore', 'oak_leaves']) {
    assert.equal(m.exclusionBreak(at(name, 0, 60, 0)), 0, name)
  }
  for (const name of ['cobblestone', 'oak_planks', 'chest', 'crafting_table', 'white_bed', 'spruce_door', 'oak_log', 'glass']) {
    assert.ok(m.exclusionBreak(at(name, 0, 60, 0)) >= 100, name)
  }
  assert.ok(m.exclusionBreak(at('stone', 21, 71, 21)) >= 100) // 家の壁
  assert.ok(m.exclusionBreak(at('dirt', 23, 69, 21)) >= 100) // 家の床のまわり
  assert.equal(m.exclusionPlace(at('air', 0, 71, 0)), 0)
  assert.ok(m.exclusionPlace(at('air', 24, 71, 21)) >= 100) // 家の 1 マス外まで置かない
  assert.deepEqual(m.scafoldingBlocks, [bot.registry.itemsByName.dirt.id])
  assert.equal(m.allow1by1towers, true)
})

test('歩きながら掘る道具が要るブロック（石にツルハシ）は、その道具を持っていないと掘らない。持てば掘る（素手で深層岩を掘り続けた）', () => {
  let m
  const registry = minecraftData('1.21.4')
  const pickaxe = registry.itemsByName.stone_pickaxe.id
  const held = []
  const bot = { registry, entities: {}, entity: { position: new Vec3(0, 70, 0) }, blockAt: () => null, inventory: { items: () => held }, pathfinder: { setMovements: (set) => { m = set } } }
  configureMovements(bot, { home: null, formerHomes: [], plan: null })
  const stone = { name: 'stone', type: 1, position: new Vec3(0, 60, 0), harvestTools: { [pickaxe]: true }, digTime: (tool) => tool === pickaxe ? 400 : 7500 }
  const dirt = { name: 'dirt', type: 2, position: new Vec3(0, 60, 1), digTime: () => 750 }
  assert.ok(m.exclusionBreak(stone) >= 100)
  assert.equal(m.exclusionBreak(dirt), 0)
  held.push({ name: 'stone_pickaxe', type: pickaxe, count: 1 })
  // 持ち物の読み直しは数秒おき
  const realNow = Date.now
  Date.now = () => realNow() + 5000
  try {
    assert.equal(m.exclusionBreak(stone), 0)
  } finally {
    Date.now = realNow
  }
})

test('水の中は高い（pathfinder の既定は 1 で、海を歩いて渡って溺れた）。陸の遠回りを選ぶ', () => {
  let m
  const bot = { registry: minecraftData('1.21.4'), entities: {}, entity: { position: new Vec3(0, 70, 0) }, blockAt: () => null, inventory: { items: () => [] }, pathfinder: { setMovements: (set) => { m = set } } }
  configureMovements(bot, { home: null, formerHomes: [], plan: null })
  assert.ok(m.liquidCost >= 5)
})

test('地下でツルハシを 1 本も持たないとき、家と地上へ帰る行動だけは素手で岩を掘る。ほかの行動は掘らない（317 m 先の地下で折れて帰れなくなった）', () => {
  let m
  const registry = minecraftData('1.21.4')
  const pickaxe = registry.itemsByName.stone_pickaxe.id
  const held = []
  const bot = { registry, entities: {}, entity: { position: new Vec3(0, 0, 0) }, blockAt: (p) => ({ name: 'stone', boundingBox: 'block', position: p }), findBlock: () => null, inventory: { items: () => held }, pathfinder: { setMovements: (set) => { m = set } } }
  const state = { home: null, formerHomes: [], plan: null, current: { verb: 'go_home' } }
  configureMovements(bot, state)
  const stone = { name: 'stone', type: 1, position: new Vec3(0, 0, 1), harvestTools: { [pickaxe]: true }, digTime: (tool) => tool === pickaxe ? 400 : 7500 }
  const later = () => { const realNow = Date.now; Date.now = () => realNow() + 5000; return () => { Date.now = realNow } }
  let restore = later()
  try {
    assert.equal(m.exclusionBreak(stone), 0, '帰る行動は掘る')
    state.current = { verb: 'explore' }
    restore(); restore = later(); Date.now = ((n) => () => n() + 10000)(Date.now)
    assert.ok(m.exclusionBreak(stone) >= 100, 'ほかの行動は掘らない')
  } finally {
    restore()
  }
})

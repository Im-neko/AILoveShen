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

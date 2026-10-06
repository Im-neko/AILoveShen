import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { ground } from '../src/candidates.mjs'
import { needs } from '../src/goals.mjs'
import { isDark } from '../src/observe.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// ボットは石の上の 0,70,0 に立つ。`roof` は頭上のブロックの高さ（null: 空が開けている）、
// `torchNear` は光が届く範囲に光源があるか
function botIn ({ roof = 73, torchNear = false } = {}, items = ['torch'], timeOfDay = 1000) {
  return {
    entity: { position: new Vec3(0.5, 70, 0.5) },
    entities: {},
    time: { timeOfDay },
    health: 20,
    food: 20,
    heldItem: null,
    registry: md,
    inventory: { items: () => items.map((name) => ({ name, count: 4 })) },
    blockAt: (p) => p.y < 70 || p.y === roof
      ? { name: 'stone', boundingBox: 'block' }
      : { name: 'cave_air', boundingBox: 'empty' },
    findBlock: () => (torchNear ? { name: 'wall_torch' } : null)
  }
}

const offered = (bot) => {
  const state = { home: null, plan: null, unreachableDrops: new Set() }
  return ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.some((c) => c.verb === 'place_torch')
}

test('暗い所（洞窟）では松明を候補に出し、危険を体の必要として示す', () => {
  const bot = botIn()
  assert.ok(offered(bot))
  assert.ok(needs(bot, { home: null }).some((n) => n.startsWith('dark here')))
})

test('明るい所、空の下、持っていないときは松明を出さない', () => {
  assert.ok(!offered(botIn({ torchNear: true })), '前に置いた松明で明るい')
  assert.ok(!offered(botIn({ roof: null }, ['torch'], 18000)), '夜の空の下: 地上は照らさない')
  assert.ok(!offered(botIn({}, [])), '松明を持っていない')
})

// 0,70,0 に立ち、`roofAt(x, z)` がその柱の頭上のブロック名（null: 空が開けている）を返す
function botUnder (roofAt) {
  const bot = botIn({ roof: null })
  bot.blockAt = (p) => {
    if (p.y < 70) return { name: 'stone', boundingBox: 'block' }
    const name = p.y === 73 ? roofAt(p.x, p.z) : null
    return name ? { name, boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }
  }
  return bot
}

test('昼の木陰（頭上が原木でも近くに空がある）や葉・ガラスの下は暗くない', () => {
  assert.ok(!isDark(botUnder((x, z) => (x === 0 && z === 0 ? 'oak_log' : null))), '幹や枝の下')
  assert.ok(!isDark(botUnder(() => 'oak_leaves')), '葉の下')
  assert.ok(!isDark(botUnder(() => 'glass')), 'ガラスの下')
})

test('空の光が届かない所（洞窟、深い屋根の下）は暗く、空が開いた柱が近ければ暗くない', () => {
  assert.ok(isDark(botUnder(() => 'stone')), '洞窟')
  assert.ok(isDark(botUnder((x, z) => (Math.abs(x) + Math.abs(z) <= 7 ? 'stone' : null))), '屋根が 7 ブロック先まで続く')
  assert.ok(!isDark(botUnder((x, z) => (Math.abs(x) + Math.abs(z) < 7 ? 'stone' : null))), '7 ブロック先に空がある')
})

test('暗い所で松明が足りなくなったら、材料があれば作る手を出す（多ければ・材料がなければ出さない）', () => {
  const craft = (counts) => {
    const bot = botIn()
    bot.inventory = { items: () => Object.entries(counts).map(([name, count]) => ({ name, count })) }
    const state = { home: null, plan: null, unreachableDrops: new Set() }
    return ground(bot, state, k, { dig: () => [], hunt: () => [] }, { leaves: [] }).candidates.find((c) => c.verb === 'craft' && c.item === 'torch')
  }
  const c = craft({ torch: 1, coal: 1, stick: 3 })
  assert.deepEqual([c.id, c.times, c.needsTable], ['craft torch x1', 1, false])
  assert.equal(craft({ torch: 1, coal: 5, stick: 3 }).times, 2)
  assert.equal(craft({ torch: 6, coal: 1, stick: 3 }), undefined, '足りている')
  assert.equal(craft({ torch: 1, stick: 3 }), undefined, '石炭も木炭もない')
})

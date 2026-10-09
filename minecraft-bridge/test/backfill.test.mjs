// 鉱石を取った穴の埋め直し（設計書 134）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { backfillHole, backfillRefusal } from '../src/backfill.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const key = (p) => `${p.x},${p.y},${p.z}`
const { signal } = new AbortController()

// y 70 の足元は石、その上は空気。blocks で上書き。置くと固いブロックになる（借りる隣が空気なら置けない）
function fakeBot ({ items = [], blocks = {}, at = new Vec3(0.5, 70, 0.5), mobs = [], health = 20 } = {}) {
  const bot = {
    entity: { position: at, height: 1.8, onGround: true },
    entities: Object.fromEntries(mobs.map((m, i) => [i + 1, { id: i + 1, type: 'hostile', name: 'zombie', ...m }])),
    health,
    time: { timeOfDay: 6000 },
    walked: [],
    placedAt: [],
    blockAt: (p) => {
      const name = blocks[key(p)] ?? (p.y < 70 ? 'stone' : 'air')
      return { name, position: p, boundingBox: /air|water|lava/.test(name) ? 'empty' : 'block' }
    },
    inventory: { items: () => items, emptySlotCount: () => 30 },
    equip: async () => {},
    waitForTicks: async () => {},
    on: () => {},
    off: () => {},
    placeBlock: async (ref, face) => {
      const item = items.find((i) => ['dirt', 'cobblestone', 'cobbled_deepslate'].includes(i.name) && i.count > 0)
      item.count--
      const at = ref.position.plus(face)
      blocks[key(at)] = item.name
      bot.placedAt.push(key(at))
    },
    pathfinder: {
      bestHarvestTool: () => null,
      goto: async (g) => { bot.walked.push(`${g.x},${g.y},${g.z}`); bot.entity.position = new Vec3(g.x + 0.5, g.y, g.z + 0.5) },
      setGoal: () => {}
    }
  }
  bot.inventory.items = () => items.filter((i) => i.count > 0)
  return bot
}
const noHome = () => ({ home: null, builds: {}, facilities: {} })

test('鉱石の穴は、手元の土でその場で埋める。水の穴用の台帳には埋めた印を付ける', async () => {
  const pos = new Vec3(1, 69, 0)
  const blocks = { [key(pos)]: 'air' }
  const items = [{ name: 'dirt', count: 3 }, { name: 'cobblestone', count: 9 }]
  const bot = fakeBot({ items, blocks })
  const state = { ...noHome(), dug: [{ x: 1, y: 69, z: 0, name: 'iron_ore', at: 0 }] }
  const note = await backfillHole(bot, state, pos, new Vec3(0, 70, 0), signal)
  assert.equal(note, '; filled the hole at 1,69,0 with dirt')
  assert.equal(blocks[key(pos)], 'dirt')
  assert.equal(items[1].count, 9, '丸石は坑道を塞ぐ分に残す')
  assert.equal(state.dug[0].plugged, true)
})

test('土がなければ丸石で埋める。どちらもなければ、開けたままだと言う', async () => {
  const pos = new Vec3(1, 69, 0)
  const blocks = { [key(pos)]: 'air' }
  const withCobble = fakeBot({ items: [{ name: 'cobblestone', count: 2 }], blocks })
  assert.match(await backfillHole(withCobble, noHome(), pos, new Vec3(0, 70, 0), signal), /filled the hole at 1,69,0 with cobblestone/)
  const none = { [key(pos)]: 'air' }
  const empty = fakeBot({ items: [], blocks: none })
  assert.match(await backfillHole(empty, noHome(), pos, new Vec3(0, 70, 0), signal), /left the hole at 1,69,0 open: no dirt or cobblestone/)
  assert.equal(none[key(pos)], 'air')
})

test('通路のセル（採掘場・家・建物の計画）は埋めない。穴を理由なしで残す', () => {
  const pos = new Vec3(1, 69, 0)
  const blocks = { [key(pos)]: 'air' }
  const bot = fakeBot({ items: [{ name: 'dirt', count: 5 }], blocks })
  const home = { min: new Vec3(0, 69, -1), max: new Vec3(2, 69, 1), cells: null }
  assert.equal(backfillRefusal(bot, { ...noHome(), home }, pos), 'passage')
  const mine = { kind: 'mine', layout: { cells: [{ x: 1, y: 69, z: 0, h: 2, part: 'tunnel_east' }] } }
  assert.equal(backfillRefusal(bot, { ...noHome(), facilities: { mine_1: mine } }, pos), 'passage')
  assert.equal(backfillRefusal(bot, noHome(), pos), '')
})

test('敵が近い・体力が低い・水が入っているときは埋めない（水の穴は plug_hole の番）', () => {
  const pos = new Vec3(1, 69, 0)
  const items = [{ name: 'dirt', count: 5 }]
  const open = { [key(pos)]: 'air' }
  assert.equal(backfillRefusal(fakeBot({ items, blocks: open, mobs: [{ position: new Vec3(4, 70, 0) }] }), noHome(), pos), 'a monster is near')
  assert.equal(backfillRefusal(fakeBot({ items, blocks: open, health: 6 }), noHome(), pos), 'low health')
  assert.match(backfillRefusal(fakeBot({ items, blocks: { [key(pos)]: 'water' } }), noHome(), pos), /water is in it/)
  assert.equal(backfillRefusal(fakeBot({ items, blocks: { [key(pos)]: 'stone' } }), noHome(), pos), 'already filled')
})

test('足元の鉱石で穴に落ちたら、掘る前に立っていたセルへ戻ってから、真下の穴を埋める', async () => {
  const pos = new Vec3(0, 69, 0) // 立っていたセル (0,70,0) の真下
  const blocks = { [key(pos)]: 'air' }
  const items = [{ name: 'dirt', count: 1 }]
  const bot = fakeBot({ items, blocks, at: new Vec3(0.5, 69, 0.5) }) // 穴に落ちている
  const note = await backfillHole(bot, noHome(), pos, new Vec3(0, 70, 0), signal)
  assert.deepEqual(bot.walked, ['0,70,0'])
  assert.match(note, /filled the hole at 0,69,0 with dirt/)
  assert.equal(blocks[key(pos)], 'dirt')
})

test('置けなくても（借りる隣がない）、掘ったことは失敗にしない', async () => {
  const pos = new Vec3(1, 69, 0)
  const blocks = { [key(pos)]: 'air' }
  const bot = fakeBot({ items: [{ name: 'dirt', count: 1 }], blocks })
  bot.placeBlock = async () => { throw new Error('no reference block') }
  assert.match(await backfillHole(bot, noHome(), pos, new Vec3(0, 70, 0), signal), /could not fill the hole at 1,69,0/)
})

// dig の行動: 鉱石だけ埋める。石や土の穴は埋めない
function diggerOf (name, blocks, items) {
  const bot = fakeBot({ items, blocks })
  const pos = new Vec3(1, 69, 0)
  blocks[key(pos)] = name
  bot.findBlocks = () => []
  bot.dig = async (b) => {
    blocks[key(b.position)] = 'air'
    items.push({ name: name === 'iron_ore' ? 'raw_iron' : 'cobblestone', count: 1 })
  }
  return { bot, pos }
}

test('dig: 鉱石を取って拾ったあと、その穴を埋めて結果に書く。石を掘った穴は埋めない', async () => {
  const oreBlocks = {}
  const oreItems = [{ name: 'dirt', count: 4 }]
  const ore = diggerOf('iron_ore', oreBlocks, oreItems)
  const r = await PRIMITIVES.dig(ore.bot, noHome(), { block: 'iron_ore', pos: ore.pos }, signal)
  assert.match(r, /^dug iron_ore, picked up 1 items; filled the hole at 1,69,0 with dirt$/)
  assert.equal(oreBlocks[key(ore.pos)], 'dirt')

  const stoneBlocks = {}
  const stoneItems = [{ name: 'dirt', count: 4 }]
  const stone = diggerOf('stone', stoneBlocks, stoneItems)
  const s = await PRIMITIVES.dig(stone.bot, noHome(), { block: 'stone', pos: stone.pos }, signal)
  assert.doesNotMatch(s, /hole/)
  assert.equal(stoneBlocks[key(stone.pos)], 'air')
})

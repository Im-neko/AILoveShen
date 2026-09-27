import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { digTargets } from '../src/world.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// 畑: y 63 が耕地、y 64 に作物（age つき）。ほかは空気。dig で空気に、種をまくと age 0 の作物に
function farm (crops, seeds = 0) {
  const cells = new Map(Object.entries(crops).map(([key, age]) => [key, { name: 'wheat', age }]))
  const inv = { wheat_seeds: seeds, wheat: 0 }
  const key = (p) => `${p.x},${p.y},${p.z}`
  const blockAt = (p) => {
    const c = cells.get(key(p))
    if (c) return { name: c.name, boundingBox: 'empty', position: p, getProperties: () => ({ age: c.age }) }
    if (p.y === 63) return { name: 'farmland', boundingBox: 'block', position: p }
    return { name: 'air', boundingBox: 'empty', position: p }
  }
  const all = () => [...cells.entries()].filter(([, c]) => c.name === 'wheat').map(([k]) => new Vec3(...k.split(',').map(Number)))
  return {
    inv,
    cells,
    registry: md,
    entity: { position: new Vec3(0, 64, 0), onGround: true },
    entities: {},
    blockAt,
    findBlocks: () => all(),
    findBlock: ({ useExtraInfo }) => all().map(blockAt).find(useExtraInfo) ?? null,
    dig: async (b) => { cells.delete(key(b.position)); inv.wheat++; inv.wheat_seeds++ },
    placeBlock: async (below) => { const p = below.position.offset(0, 1, 0); cells.set(key(p), { name: 'wheat', age: 0 }); inv.wheat_seeds-- },
    equip: async () => {},
    waitForTicks: async () => {},
    on: () => {},
    off: () => {},
    inventory: { items: () => Object.entries(inv).filter(([, n]) => n > 0).map(([name, count]) => ({ name, count })), emptySlotCount: () => 30 },
    pathfinder: { goto: async () => {}, setGoal: () => {}, bestHarvestTool: () => null }
  }
}

test('畑の作物から小麦と種が取れることを知っている（掘って取れる物として、観測に添える物として）', () => {
  assert.ok(k.blockSources.get('wheat').includes('wheat'))
  assert.deepEqual(k.dropsOf('wheat').sort(), ['wheat', 'wheat_seeds'])
  assert.ok(k.blockSources.get('carrot').includes('carrots'))
  assert.deepEqual(k.dropsOf('oak_log'), []) // 取れる物がそのブロックだけなら添えない
  assert.deepEqual(k.dropsOf('coal_ore'), ['coal'])
})

test('掘る対象の作物は熟したものだけ（育ち途中を掘ると種しか出ず、畑が減る）', () => {
  const bot = farm({ '1,64,0': 7, '2,64,0': 3 })
  assert.deepEqual(digTargets(bot, { unreachable: new Map() }, 'wheat').map((p) => p.x), [1])
})

test('熟した小麦は、まわりの熟したものも続けて収穫し、拾った種で植え直す', async () => {
  const bot = farm({ '1,64,0': 7, '2,64,0': 7, '3,64,0': 2 })
  const r = await PRIMITIVES.dig(bot, { unreachableDrops: new Set() }, { block: 'wheat', pos: new Vec3(1, 64, 0) }, new AbortController().signal)
  assert.match(r, /^harvested 2 wheat, replanted 2/)
  assert.equal(bot.inv.wheat, 2)
  assert.equal(bot.cells.get('1,64,0').age, 0) // 植え直した
  assert.equal(bot.cells.get('3,64,0').age, 2) // 育ち途中はそのまま
})

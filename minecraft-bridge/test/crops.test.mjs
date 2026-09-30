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

test('畑の小麦が育ち途中なら遠出しない。熟していれば畑へ戻る。畑がなければ今までどおり探す', async () => {
  const { solve } = await import('../src/solver.mjs')
  const farm = (f) => (block) => block === 'wheat' ? { planted: 9, ages: [1, 5], mature: 7, free: 0, pos: { x: -67, y: 65, z: -152 }, ...f } : null
  const world = (f) => ({ inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, remembered: new Set(['wheat']), farm: f })
  const need = [{ spec: 'wheat', count: 6 }]

  // 2026-09-27: 家の小麦が age 1〜5 のまま、261 m 先の小麦へ遠征しようとした
  const growing = solve(k, world(farm({ ripe: 0 })), need)
  assert.deepEqual(growing.leaves, [])
  assert.match(growing.blocked.join('\n'), /wheat on the farms for wheat is still growing \(9 planted, none ripe, age 1-5 of 7\).*grows only while you stay near it/)
  // 空きがなければ、別の名前の畑を足す（設計してから。施設は広げられない。設計書 47 §14）
  assert.match(growing.blocked.join('\n'), /add another wheat farm with a new name \(facility\(<new name>\)/)

  // 空きがあれば、育つ間に埋める: 種があればまく、クワがあれば耕す（ついでの作業: 停滞に数えない）
  const tools = solve(k, { ...world(farm({ ripe: 0, free: 7 })), inventory: { wheat_seeds: 5, stone_hoe: 1 } }, need)
  assert.deepEqual(tools.leaves.map((l) => [l.kind, l.item, l.crop, !!l.also]), [['sow', 'wheat_seeds', 'wheat', true], ['till', undefined, 'wheat', true]])
  assert.match(tools.blocked.join('\n'), /fill the farms' 7 free cells \(till and sow wheat_seeds; a step farmed\(wheat, 16\)\)/)
  // 種もクワもなければ、それを手に入れる手も出す（クワが壊れ、種を使い切った畑で「待つ」しか残らなかった）
  const short = solve(k, { ...world(farm({ ripe: 0, free: 7 })), blocks: { short_grass: 4 }, inventory: { stick: 2, oak_planks: 4 }, table: { x: 0, y: 64, z: 0 } }, need)
  assert.deepEqual(short.leaves.map((l) => [l.kind, l.item, !!l.also]), [['dig', 'wheat_seeds', true], ['craft', 'wooden_hoe', true]])
  assert.deepEqual(short.leaves[0].sources, ['short_grass'])
  assert.match(short.blocked.join('\n'), /no wheat_seeds carried: dig grass for seeds; no hoe carried: make one/)
  // 耕し済みのマス（sow）に足りるだけの種しかなければ耕さない: 耕しただけで何も植わっていない地面は、水が
  // 近くにないと土に戻る（2026-09-29: 種がないまま耕し続けた）。クワだけ持っていても耕さず、種を集める
  const kinds = (r) => r.leaves.map((l) => [l.kind, l.item])
  assert.deepEqual(kinds(solve(k, { ...world(farm({ ripe: 0, free: 7, sow: 3 })), inventory: { wheat_seeds: 3, stone_hoe: 1 } }, need)), [['sow', 'wheat_seeds']])
  assert.deepEqual(kinds(solve(k, { ...world(farm({ ripe: 0, free: 7, sow: 3 })), inventory: { wheat_seeds: 4, stone_hoe: 1 } }, need)), [['sow', 'wheat_seeds'], ['till', undefined]])
  const hoeOnly = solve(k, { ...world(farm({ ripe: 0, free: 7, sow: 2 })), blocks: { short_grass: 4 }, inventory: { stone_hoe: 1 } }, need)
  assert.deepEqual(kinds(hoeOnly), [['dig', 'wheat_seeds']])
  assert.match(hoeOnly.blocked.join('\n'), /do not till more than you can sow/)
  // 草が見えなければ、種を探しに畑から離れる手は出さない（育つ間は畑のそば）
  assert.deepEqual(solve(k, { ...world(farm({ ripe: 0, free: 7 })), inventory: { stone_hoe: 1 } }, need).leaves, [])
  const full = solve(k, { ...world(farm({ ripe: 0 })), inventory: { wheat_seeds: 5, stone_hoe: 1 } }, need)
  assert.deepEqual(full.leaves, [])

  const ripe = solve(k, world(farm({ ripe: 3 })), need)
  assert.deepEqual(ripe.leaves.map((l) => [l.kind, l.farm?.ripe]), [['explore', 3]])

  const none = solve(k, world(() => null), need)
  assert.deepEqual(none.leaves.map((l) => [l.kind, l.farm]), [['explore', undefined]])
})

// 設計書 47 §17: 水のバケツは、空のバケツで水源の水を汲む（クラフトでも掘るでもない）
test('have(water_bucket): バケツを作り、水源で汲む。水が見えなければ水を探す', async () => {
  const { solve } = await import('../src/solver.mjs')
  const spot = { x: 3, y: 63, z: 4 }
  const world = (extra) => ({ inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true, remembered: new Set(), water: () => null, ...extra })
  const need = [{ spec: 'water_bucket', count: 1 }]
  const fill = solve(k, world({ inventory: { bucket: 1 }, water: () => spot }), need)
  assert.deepEqual(fill.leaves.map((l) => [l.kind, l.item, l.count]), [['fill', 'water_bucket', 1]])
  assert.deepEqual(fill.leaves[0].pos, spot)
  assert.equal(fill.met, false)
  // 水が見えなければ探す（バケツは持っている）
  const seek = solve(k, world({ inventory: { bucket: 1 } }), need)
  assert.deepEqual(seek.leaves.map((l) => [l.kind, l.sources]), [['explore', ['water']]])
  // バケツがなければ、鉄 3 つから作る（鉄インゴットがあれば、作業台でクラフト）
  const make = solve(k, world({ inventory: { iron_ingot: 3 }, table: { x: 0, y: 64, z: 0 }, water: () => spot }), need)
  assert.deepEqual(make.leaves.map((l) => [l.kind, l.item]), [['craft', 'bucket']])
  assert.doesNotMatch(make.blocked.join('\n'), /no way to get water_bucket/)
  // 汲んだあとは持ち物のバケツが 1 つ減る（同じバケツで 2 つは作れない）
  const two = solve(k, world({ inventory: { bucket: 1 }, water: () => spot }), [{ spec: 'water_bucket', count: 2 }])
  assert.ok(two.leaves.every((l) => l.kind !== 'fill' || l.count === 2))
})

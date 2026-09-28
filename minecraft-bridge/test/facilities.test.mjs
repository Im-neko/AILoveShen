import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { adoptExisting, facilitiesStatus, plantingGroups } from '../src/facilities.mjs'
import { memoryList, newMemory } from '../src/memory.mjs'
import { bearing } from '../src/observe.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const v = (x, y, z) => new Vec3(x, y, z)

// 家（室内 0..2 x 0..2、床 y 70、扉 (1,70,3)）、家の東に耕地 3 マス（y 69）と小麦、植えた苗木 2 本（北）と 1 本（遠く）
function world () {
  const blocks = new Map()
  const set = (x, y, z, name, props = {}) => blocks.set(`${x},${y},${z}`, { name, props })
  for (const x of [10, 11, 12]) set(x, 69, 0, 'farmland')
  set(10, 70, 0, 'wheat', { age: 7 })
  set(11, 70, 0, 'wheat', { age: 3 })
  set(0, 70, -10, 'oak_sapling')
  set(2, 70, -11, 'oak_log')
  set(60, 70, 60, 'oak_sapling')
  const blockAt = (p) => {
    const b = blocks.get(`${p.x},${p.y},${p.z}`)
    return { name: b?.name ?? (p.y < 70 ? 'dirt' : 'air'), position: p, getProperties: () => b?.props ?? {} }
  }
  const byId = new Map(Object.values(md.blocksByName).map((b) => [b.id, b.name]))
  return {
    registry: md,
    entity: { position: v(1.5, 70, 5.5) },
    blockAt,
    findBlocks ({ matching, maxDistance, point }) {
      const names = new Set([matching].flat().map((id) => byId.get(id)))
      return [...blocks.entries()].filter(([, b]) => names.has(b.name)).map(([k]) => v(...k.split(',').map(Number)))
        .filter((p) => p.distanceTo(point) <= maxDistance)
    }
  }
}

const home = () => ({ name: '小屋', min: v(0, 70, 0), max: v(2, 70, 2), door: v(1, 70, 3), inside: v(1, 70, 2), outside: v(1, 70, 4) })

test('今ある家・耕地・植えた苗木を施設として取り込む（植えた苗木は近いものどうしで 1 つ）', () => {
  const bot = world()
  const state = {
    home: home(),
    plantings: [{ x: 0, y: 70, z: -10, item: 'oak_sapling' }, { x: 2, y: 70, z: -11, item: 'oak_sapling' }, { x: 60, y: 70, z: 60, item: 'oak_sapling' }]
  }
  assert.equal(adoptExisting(bot, state), true)
  assert.deepEqual(Object.keys(state.facilities).sort(), ['home', 'home_farm', 'tree_farm_1', 'tree_farm_2'])
  assert.deepEqual(state.facilities.home_farm.box, { min: { x: 10, y: 69, z: 0 }, max: { x: 12, y: 69, z: 0 } })
  assert.equal(adoptExisting(bot, state), false) // 2 回目は変わらない

  const list = Object.fromEntries(facilitiesStatus(bot, state).map((f) => [f.name, f]))
  assert.deepEqual(list.home_farm.contents, { crops: { wheat: { planted: 2, ripe: 1, ja: '' } } })
  assert.equal(list.home.entrance.z, 4)
  const trees = [list.tree_farm_1, list.tree_farm_2].find((t) => t.box.min.x <= 0)
  assert.deepEqual(trees.contents, { saplings: 1, trees: 1, empty: 0 })
  assert.equal(facilitiesStatus(bot, state)[0].name, 'home') // 近い順
})

test('植えた苗木のかたまり: 8 マス以内でつながるものは 1 つ', () => {
  const groups = plantingGroups([{ x: 0, z: 0 }, { x: 20, z: 0 }, { x: 7, z: 0 }, { x: 14, z: 0 }])
  assert.equal(groups.length, 1)
  assert.equal(plantingGroups([{ x: 0, z: 0 }, { x: 30, z: 0 }]).length, 2)
})

test('覚えている場所を種類ごとに 1 件、座標は近い順、取れる物と日本語名つきで返す', () => {
  const memory = newMemory()
  memory.places.wheat = [{ x: 100, y: 64, z: 0, count: 8, seen: 0 }, { x: 10, y: 64, z: 0, count: 3, seen: 1200 }]
  memory.deaths.push({ x: 0, y: 64, z: 300, seen: 0 })
  const ja = { wheat: '小麦', wheat_seeds: '小麦の種' }
  const out = memoryList(memory, v(0, 64, 0), 2400, bearing, (k) => (k === 'wheat' ? ['wheat', 'wheat_seeds'] : []), (n) => ja[n] ?? '')
  assert.equal(out.places.length, 1)
  const [wheat] = out.places
  assert.equal(wheat.ja, '小麦')
  assert.equal(wheat.places_known, 2)
  assert.deepEqual(wheat.nearest.map((p) => [p.x, p.distance_m, p.minutes_ago]), [[10, 10, 1], [100, 100, 2]])
  assert.deepEqual(wheat.gives_ja, ['小麦', '小麦の種'])
  assert.equal(out.deaths[0].direction, 'S')
})

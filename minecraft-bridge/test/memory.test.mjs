import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { newMemory, remember, recall, rememberDeath, summarizeMemory, visited, frontierDistance, MAX_PLACES_PER_KIND, ANIMAL_STALE_TICKS } from '../src/memory.mjs'
import { bearing } from '../src/observe.mjs'
import { ground } from '../src/candidates.mjs'

const { Vec3 } = vec3Pkg
const at = (x, z) => ({ x, y: 70, z })
const sheep = (x, z) => ({ kind: 'sheep', pos: at(x, z) })

test('見たものは 16x16 の区画ごとに、数といつ見たかと一緒に覚える', () => {
  const m = newMemory()
  remember(m, [sheep(100, 100), sheep(101, 102), sheep(140, 100)], at(0, 0), 500)
  assert.deepEqual(m.places.sheep.map((p) => [p.region, p.count, p.seen]), [['6,6', 2, 500], ['8,6', 1, 500]])
  assert.ok(visited(m, at(3, 3)))
  assert.ok(!visited(m, at(40, 3)))
})

test('近くで覚えていた場所が今見えなければ忘れる。遠い場所は残す', () => {
  const m = newMemory()
  remember(m, [sheep(10, 10), sheep(200, 200)], at(0, 0), 0)
  remember(m, [], at(12, 12), 100) // 戻ってきたら何もない
  assert.deepEqual(m.places.sheep.map((p) => p.region), ['12,12'])
})

test('種類ごとに一番新しい場所だけを残す', () => {
  const m = newMemory()
  for (let i = 0; i < MAX_PLACES_PER_KIND + 3; i++) remember(m, [sheep(1000 + i * 32, 0)], at(1000 + i * 32, 0), i)
  assert.equal(m.places.sheep.length, MAX_PLACES_PER_KIND)
  assert.equal(m.places.sheep[0].seen, MAX_PLACES_PER_KIND + 2)
})

test('recall: 見えていない新しい場所を近い順に返す。動物は 1 日で古くなり、原木は古くならない', () => {
  const m = newMemory()
  remember(m, [sheep(100, 0), sheep(300, 0), { kind: 'oak_log', pos: at(0, 200) }, sheep(10, 0)], at(500, 500), 0)
  const me = at(0, 0)
  assert.deepEqual(recall(m, ['sheep'], me, 1200).map((p) => [p.x, p.distance, p.minutesAgo]), [[100, 100, 1], [300, 300, 1]])
  assert.deepEqual(recall(m, ['sheep'], me, ANIMAL_STALE_TICKS + 1), [])
  assert.equal(recall(m, ['oak_log'], me, ANIMAL_STALE_TICKS * 5).length, 1)
})

test('要約には種類ごとの一番近い場所と、死んだ場所を出す', () => {
  const m = newMemory()
  remember(m, [sheep(0, -100), sheep(0, -300), { kind: 'iron_ore', pos: at(50, 0) }], at(1000, 1000), 0)
  rememberDeath(m, at(0, 40), 0)
  const s = summarizeMemory(m, new Vec3(0, 70, 0), 2400, bearing)
  assert.deepEqual(s.places, [
    { kind: 'iron_ore', count: 1, direction: 'E', distance_m: 50, minutes_ago: 2 },
    { kind: 'sheep', count: 1, direction: 'N', distance_m: 100, minutes_ago: 2 }
  ])
  assert.deepEqual(s.deaths, [{ direction: 'S', distance_m: 40, minutes_ago: 2 }])
})

test('探すときは覚えている場所を先に出し、次にまだ行っていない方角を出す', () => {
  const memory = newMemory()
  remember(memory, [sheep(0, -100)], at(0, 0), 0) // 最初の区画は行ったことがある
  remember(memory, [], at(24, 0), 0) // 東の区画も同じ
  const bot = {
    entity: { position: new Vec3(0, 70, 0) },
    entities: {},
    time: { timeOfDay: 1000, age: 1200 },
    health: 20,
    food: 20,
    heldItem: null,
    blockAt: () => null, // 明るさのデータがない: 暗いとは判定しない
    inventory: { items: () => [] }
  }
  const state = { home: null, plan: null, unreachableDrops: new Set(), memory }
  const leaf = { kind: 'explore', item: 'wool', sources: ['sheep'] }
  const { candidates } = ground(bot, state, null, { dig: () => [], hunt: () => [] }, { leaves: [leaf] })
  assert.equal(candidates[0].id, 'go to sheep seen at 0,-100')
  assert.equal(candidates[0].verb, 'goto_memory')
  const east = candidates.find((c) => c.id.startsWith('explore east'))
  assert.equal(east.been_there, true)
  // 東は見た所が続くので、その先のまだ見ていない土地まで行く（行き来をくり返さない）
  assert.equal(east.id, 'explore east (unexplored land 48m away)')
  assert.equal(east.go, 48)
  assert.equal(candidates.at(-1), east)
})

test('まわりを見尽くしたら、まだ見ていない土地まで区間ごとに進む（原木がなく同じ所を探し続けた）', () => {
  const memory = newMemory()
  for (let x = -96; x <= 96; x += 16) for (let z = -96; z <= 96; z += 16) remember(memory, [], at(x, z), 0)
  assert.equal(frontierDistance(memory, { x: 0, z: 0 }, 1, 0), 128)
  assert.equal(frontierDistance(memory, { x: 0, z: 0 }, 0, -1), 128)
  assert.equal(frontierDistance(newMemory(), { x: 0, z: 0 }, 1, 0), 16)
})

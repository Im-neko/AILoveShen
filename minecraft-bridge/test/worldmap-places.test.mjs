import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { WorldMap, makeClassifier, AIR, minuteOf } from '../src/worldmap.mjs'
import { mapFind, mapPlaces, memoryView, rememberedKinds, usable } from '../src/worldmap-places.mjs'
import { recall, newMemory, summarizeMemory } from '../src/memory.mjs'

const md = minecraftData('1.21.4')
const cls = makeClassifier(md)
const cell = (name) => cls.cellOf(name)
const NOW = 1200 * 100 // ワールドの 100 分

function fixture () {
  const worldMap = new WorldMap()
  const state = { worldMap, mapCells: cls, home: null, plan: null, memory: newMemory(), placed: {} }
  // 空気に接している印に、上のセルを空気にする
  const put = (name, x, y, z, minute = 90) => {
    worldMap.set(x, y, z, cell(name), minute)
    worldMap.set(x, y + 1, z, AIR, minute)
  }
  const tree = (x, z) => {
    for (let y = 70; y <= 73; y++) worldMap.set(x, y, z, cell('oak_log'), 90)
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) worldMap.set(x + dx, 74, z + dz, cell('oak_leaves'), 90)
    worldMap.set(x + 1, 71, z, AIR, 90)
  }
  return { state, worldMap, put, tree }
}

const me = { x: 0, y: 70, z: 0 }

test('地図から、木の原木を区域ごとに一番近い 1 点で出す（数は概数）', () => {
  const { state, tree } = fixture()
  tree(50, 0)
  tree(60, 0) // 同じ区域（48..63）
  tree(100, 0)
  const places = mapPlaces(state, me, NOW, ['oak_log'])
  const list = places.oak_log.sort((a, b) => a.x - b.x)
  assert.equal(list.length, 2)
  assert.equal(list[0].x, 50)
  assert.equal(list[0].count, 2, "同じ区域の空気に接する原木の数")
  assert.equal(list[0].seen, NOW - 10 * 1200, '10 分前に見た')
})

test('葉のない原木（村の柱や畑の縁）は出さない。自分で置いた原木は出す', () => {
  const { state, put, worldMap } = fixture()
  for (let y = 70; y <= 72; y++) worldMap.set(80, y, 0, cell('oak_log'), 90)
  worldMap.set(81, 71, 0, AIR, 90)
  assert.deepEqual(mapPlaces(state, me, NOW, ['oak_log']), {})
  state.placed['80,71,0'] = { name: 'oak_log', at: 0 }
  assert.equal(mapPlaces({ ...state }, { x: 1, y: 70, z: 0 }, NOW, ['oak_log']).oak_log.length, 1)
  put('coal_ore', 90, 60, 0)
  assert.ok(usable(state, 90, 60, 0, cell('coal_ore')))
})

test('埋まった鉱石は出さない（空気に接するものだけ）。水に接した石炭も出さない', () => {
  const { state, worldMap, put } = fixture()
  worldMap.set(40, 60, 0, cell('coal_ore'), 90) // 周りが未知: 埋まっている
  worldMap.set(41, 60, 0, cell('stone'), 90)
  put('coal_ore', 60, 65, 0)
  put('coal_ore', 70, 65, 0)
  worldMap.set(71, 65, 0, cell('water'), 90) // 水に接している
  const list = mapPlaces(state, me, NOW, ['coal_ore']).coal_ore
  assert.deepEqual(list.map((p) => p.x), [60])
})

test('家と建物の中は出さない', () => {
  const { state, put } = fixture()
  put('coal_ore', 60, 65, 0)
  state.home = { min: { x: 58, y: 65, z: -2 }, max: { x: 62, y: 65, z: 2 } }
  assert.deepEqual(mapPlaces(state, me, NOW, ['coal_ore']), {})
})

test('memoryView: 動物は memory のまま、ブロックは地図から（古い memory のブロックは使わない）', () => {
  const { state, put } = fixture()
  put('coal_ore', 60, 65, 0)
  state.memory.places = {
    cow: [{ region: '3,0', x: 50, y: 70, z: 0, count: 1, seen: NOW }],
    iron_ore: [{ region: '0,0', x: 9, y: 9, z: 9, count: 1, seen: 0 }]
  }
  const view = memoryView(state, me, NOW, ['coal_ore', 'iron_ore'])
  assert.deepEqual(Object.keys(view.places).sort(), ['coal_ore', 'cow'])
  assert.equal(state.memory.places.iron_ore.length, 1, '元の memory は書き換えない')
  // 地図がなければ memory のまま
  assert.equal(memoryView({ memory: state.memory }, me, NOW), state.memory)
})

test('思い出した場所（recall）は 32 m より遠いものだけ。地図で見た遠い木が候補になる', () => {
  const { state, put } = fixture()
  put('coal_ore', 10, 65, 0) // 近い
  put('coal_ore', 60, 65, 0)
  const view = memoryView(state, me, NOW, ['coal_ore'])
  const out = recall(view, ['coal_ore'], me, NOW)
  assert.deepEqual(out.map((p) => p.x), [60])
  assert.equal(out[0].minutesAgo, 10)
  assert.ok(rememberedKinds(state, me, NOW).has('coal_ore'))
  assert.equal(rememberedKinds(state, me, NOW).has('iron_ore'), false)
  assert.equal(rememberedKinds(state, me, NOW).has('nothing_like_this'), false)
})

test('観察の要約に出る（前と同じ書式）。種類を決めない引き方は地形を出さない', () => {
  const { state, put, worldMap } = fixture()
  put('coal_ore', 60, 65, 0)
  put('sand', 0, 65, 40)
  put('grass_block', 0, 65, 20)
  const summary = summarizeMemory(memoryView(state, me, NOW, ['coal_ore']), me, NOW, () => 'east')
  assert.deepEqual(summary.places.map((p) => p.kind), ['coal_ore'])
  const all = Object.keys(mapPlaces(state, me, NOW, null)).sort()
  assert.deepEqual(all, ['coal_ore', 'sand'])
  assert.ok(worldMap.sectionCount > 0)
})

test('mapFind: 近い順。読み込まれていて実物が違うものは出さず、地図を直す。読み込まれていなければ出す', () => {
  const { state, put } = fixture()
  put('oak_log', 30, 72, 0)
  put('oak_log', 60, 72, 0)
  put('oak_log', 80, 72, 0)
  const worldMap = state.worldMap
  for (const x of [30, 60, 80]) for (let dx = -1; dx <= 1; dx++) worldMap.set(x + dx, 74, 1, cell('oak_leaves'), 90)
  const live = (x) => (x === 30 ? 'air' : x === 60 ? 'oak_log' : undefined)
  const out = mapFind(state, 'oak_log', me, 96, { ticksNow: NOW, live })
  assert.deepEqual(out.map((o) => o.x), [60, 80])
  assert.equal(worldMap.cellAt(30, 72, 0), AIR, '実物が違うので地図を直した')
  assert.equal(out[0].minutes_ago, 10)
  assert.deepEqual(mapFind(state, 'no_such_block', me), [])
})

test('種類を決めない引き方は、葉・砂利・石の仲間・草花・水を出さない。名指しすれば出る', () => {
  const { state, put } = fixture()
  for (const [i, name] of ['oak_leaves', 'gravel', 'andesite', 'poppy', 'water', 'fern', 'coal_ore', 'sand', 'oak_planks'].entries()) put(name, 40 + i * 16, 65, 0)
  const all = Object.keys(mapPlaces(state, me, NOW, null)).sort()
  assert.deepEqual(all, ['coal_ore', 'oak_planks', 'sand'])
  assert.equal(mapPlaces(state, me, NOW, ['gravel']).gravel.length, 1)
})

test('rememberedKinds: 種類がいくつあっても、地図をなめるのは 1 回', () => {
  const { state, put, worldMap } = fixture()
  put('coal_ore', 60, 65, 0)
  let scans = 0
  const regionReps = worldMap.regionReps.bind(worldMap)
  worldMap.regionReps = (...args) => { scans++; return regionReps(...args) }
  const remembered = rememberedKinds(state, me, NOW)
  for (const k of ['oak_log', 'birch_log', 'spruce_log', 'coal_ore', 'iron_ore', 'wheat', 'stone']) remembered.has(k)
  assert.equal(scans, 1)
  assert.equal(remembered.has('coal_ore'), true)
  assert.equal(remembered.has('oak_planks'), false, '要約の種類にないものは、前と同じく覚えていない扱い')
})

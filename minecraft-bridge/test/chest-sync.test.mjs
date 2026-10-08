// 家にあるチェストを記録と合わせる（設計書 121）: 置いたのに記録されないチェストが使われなかった
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { newMemory, rememberChest, homeChests } from '../src/memory.mjs'
import { syncHomeChests, storageUsage, roomyChests } from '../src/storage.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
// y 70 に 3x3 の室内（x 1..3、z 1..3）
const home = { door: v(2, 70, 4), inside: v(2, 70, 3), outside: v(2, 70, 5), min: v(1, 70, 1), max: v(3, 70, 3), bed: null, breach: [] }

// chests: { 'x,y,z': { type } }。unloaded: 読み込まれていないマス
function botWith (chests, { unloaded = [] } = {}) {
  return {
    time: { age: 500 },
    registry: { itemsByName: {} },
    blockAt: (p) => {
      const key = `${p.x},${p.y},${p.z}`
      if (unloaded.includes(key)) return null
      const c = chests[key]
      if (c) return { name: 'chest', boundingBox: 'block', position: p, getProperties: () => ({ type: c.type ?? 'single', facing: c.facing ?? 'east' }) }
      return { name: p.y < 70 ? 'stone' : 'air', boundingBox: p.y < 70 ? 'block' : 'empty', position: p }
    }
  }
}
const stateOf = () => ({ home, memory: newMemory() })

test('置いたのに記録のないチェストを、空きとして覚える（開いて確かめたら本当の中身に直る）', () => {
  const state = stateOf()
  rememberChest(state.memory, v(1, 70, 1), { oak_log: 99 }, 10, { used: 27, size: 27 }) // 満杯の 1 つだけ知っている
  const bot = botWith({ '1,70,1': {}, '1,70,2': {}, '3,70,1': {}, '3,70,3': {} })
  assert.equal(storageUsage(bot, state).free, 0)
  syncHomeChests(bot, state)
  assert.equal(homeChests(state.memory, home).length, 4)
  const u = storageUsage(bot, state)
  assert.deepEqual([u.chests, u.size, u.free], [4, 108, 81])
  // 知っていた満杯のチェストの記録は壊さない
  assert.deepEqual(state.memory.chests['1,70,1'].contents, { oak_log: 99 })
  // もう一度呼んでも増えも変わりもしない
  syncHomeChests(bot, state)
  assert.equal(homeChests(state.memory, home).length, 4)
  assert.deepEqual(state.memory.chests['1,70,2'].contents, {})
})

test('なくなったチェストの記録は消す。読み込まれていないマスは触らない。家の外のチェストは覚えない', () => {
  const state = stateOf()
  rememberChest(state.memory, v(1, 70, 1), { dirt: 5 }, 10)
  rememberChest(state.memory, v(3, 70, 3), { dirt: 5 }, 10)
  const gone = botWith({ '3,70,3': {}, '10,70,10': {} }, {})
  syncHomeChests(gone, state)
  assert.deepEqual(Object.keys(state.memory.chests), ['3,70,3'])
  // 読み込まれていないマスの記録は残す
  const state2 = stateOf()
  rememberChest(state2.memory, v(1, 70, 1), { dirt: 5 }, 10)
  syncHomeChests(botWith({}, { unloaded: ['1,70,1'] }), state2)
  assert.ok(state2.memory.chests['1,70,1'])
  assert.ok(!state.memory.chests['10,70,10'])
})

test('ラージチェスト（隣り合う 2 つ）は左の半分に 54 スロットで覚え、右の半分は別に数えない', () => {
  const state = stateOf()
  const bot = botWith({ '1,70,1': { type: 'left', facing: 'east' }, '1,70,2': { type: 'right', facing: 'east' } })
  // chestRecordPos は隣の同じ向きのチェストを探す
  syncHomeChests(bot, state)
  assert.equal(homeChests(state.memory, home).length, 1)
  const [c] = homeChests(state.memory, home)
  assert.equal(c.slots.size, 54)
  // 以前に単独として覚えた右の半分の記録は消える
  const state2 = stateOf()
  rememberChest(state2.memory, v(1, 70, 2), { dirt: 1 }, 10, { used: 1, size: 27 })
  syncHomeChests(bot, state2)
  assert.ok(!state2.memory.chests['1,70,2'])
  assert.equal(state2.memory.chests['1,70,1'].slots.size, 54)
})

test('家がない・記録がないときは何もしない', () => {
  assert.doesNotThrow(() => syncHomeChests(botWith({}), { home: null, memory: newMemory() }))
  assert.doesNotThrow(() => syncHomeChests(botWith({}), { home, memory: null }))
})

test('預け先は空きのあるチェストが先（満杯の記録のチェストを毎回先に開けない）。空きの中ではボットに近い順', () => {
  const state = stateOf()
  rememberChest(state.memory, v(1, 70, 1), { oak_log: 99 }, 10, { used: 27, size: 27 }) // 満杯、ボットのすぐそば
  rememberChest(state.memory, v(3, 70, 3), {}, 10, { used: 0, size: 27 })
  rememberChest(state.memory, v(3, 70, 1), {}, 10, { used: 0, size: 27 })
  const bot = { ...botWith({}), entity: { position: v(1.5, 70, 1.5) } }
  assert.deepEqual(roomyChests(bot, state).map((c) => `${c.x},${c.z}`), ['3,1', '3,3', '1,1'])
  // 全部満杯なら、近い順
  const full = stateOf()
  rememberChest(full.memory, v(1, 70, 1), { a: 99 }, 10, { used: 27, size: 27 })
  rememberChest(full.memory, v(3, 70, 3), { a: 99 }, 10, { used: 27, size: 27 })
  assert.equal(roomyChests(bot, full)[0].x, 1)
})

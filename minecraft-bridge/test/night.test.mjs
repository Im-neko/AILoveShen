import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate } from '../src/goals.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// 夜（寝られる時刻）の外。地面は y 63、上は空気（covered なら頭の上に石）
function night ({ bed = null, items = {}, covered = false, y = 64 } = {}) {
  return {
    registry: md,
    entity: { position: new Vec3(0, y, 0) },
    time: { timeOfDay: 14000 },
    isSleeping: false,
    inventory: { items: () => Object.entries(items).map(([name, count]) => ({ name, count })) },
    findBlocks: () => (bed ? [bed] : []),
    blockAt: (p) => {
      if (covered && p.y > y + 1 && p.y < y + 6) return { name: 'stone', boundingBox: 'block' }
      return { name: p.y < 64 ? 'dirt' : 'air', boundingBox: p.y < 64 ? 'block' : 'empty' }
    }
  }
}
const kinds = (bot, state = { home: null, plan: null }) => {
  const goal = makeGoal({ predicate: 'through_night' }, bot, state, k)
  return evaluate(bot, { ...state, goal }, k, {}).leaves.map((l) => l.kind)
}

test('夜は家がなくても越せる: 近くのベッドで寝る、持っているベッドを置いて寝る、地下ならそのまま', () => {
  assert.deepEqual(kinds(night({ bed: new Vec3(10, 64, 3) })), ['sleep_at', 'dig_in']) // 家がない夜は、穴を掘って塞ぐ手も出す
  assert.deepEqual(kinds(night({ items: { white_bed: 1 } })), ['bed_here', 'dig_in'])
  assert.deepEqual(kinds(night({ covered: true, y: 40 })), ['stay_underground'])
  // 家があれば帰るのも選択肢の 1 つ
  const home = { door: new Vec3(40, 64, 40), inside: new Vec3(40, 64, 39), outside: new Vec3(40, 64, 41), min: new Vec3(39, 64, 38), max: new Vec3(41, 64, 39), bed: null, breach: [] }
  assert.deepEqual(kinds(night({ items: { white_bed: 1 } }), { home, plan: null }), ['go_home', 'bed_here'])
})

test('行けなかったベッドと家はしばらく出さず、持っているベッドは近くのベッドがあっても選べる', async () => {
  const { markNoPath } = await import('../src/move.mjs')
  const bed = new Vec3(10, 64, 3)
  const home = { door: new Vec3(40, 64, 40), inside: new Vec3(40, 64, 39), outside: new Vec3(40, 64, 41), min: new Vec3(39, 64, 38), max: new Vec3(41, 64, 39), bed: null, breach: [] }
  const state = { home, plan: null }
  const bot = night({ bed, items: { white_bed: 1 } })
  assert.deepEqual(kinds(bot, state), ['go_home', 'sleep_at', 'bed_here'])
  markNoPath(state, bed)
  markNoPath(state, home.outside)
  assert.deepEqual(kinds(bot, state), ['bed_here', 'dig_in'])
  // 前の家の中のベッドは出さない（閉じていて入れないことがある）
  const former = { ...home, min: new Vec3(9, 64, 2), max: new Vec3(11, 64, 4) }
  assert.deepEqual(kinds(night({ bed }), { home: null, formerHomes: [former], plan: null }), ['dig_in'])
})

test('家がなくても、塞いだ穴の中なら朝までそこにいる（ほかの越し方は出さない）。家が近ければ穴は出さない', () => {
  // 足と頭のまわりが土、上にも下にも土
  const bot = night({ y: 61 })
  const inner = bot.blockAt
  bot.blockAt = (p) => {
    const inShaft = Math.floor(p.x) === 0 && Math.floor(p.z) === 0 && p.y >= 61 && p.y < 63
    if (inShaft) return { name: 'air', boundingBox: 'empty', position: p }
    return { ...inner(p), position: p, name: 'dirt', boundingBox: 'block' }
  }
  assert.deepEqual(kinds(bot), ['stay_burrowed'])
  const home = { door: new Vec3(10, 64, 10), inside: new Vec3(10, 64, 9), outside: new Vec3(10, 64, 11), min: new Vec3(9, 64, 8), max: new Vec3(11, 64, 9), bed: null, breach: [] }
  assert.deepEqual(kinds(night({}), { home, plan: null }), ['go_home']) // 家が近い（64 ブロック以内）
})

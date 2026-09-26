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
  assert.deepEqual(kinds(night({ bed: new Vec3(10, 64, 3) })), ['sleep_at'])
  assert.deepEqual(kinds(night({ items: { white_bed: 1 } })), ['bed_here'])
  assert.deepEqual(kinds(night({ covered: true, y: 40 })), ['stay_underground'])
  // 家があれば帰るのも選択肢の 1 つ
  const home = { door: new Vec3(40, 64, 40), inside: new Vec3(40, 64, 39), outside: new Vec3(40, 64, 41), min: new Vec3(39, 64, 38), max: new Vec3(41, 64, 39), bed: null, breach: [] }
  assert.deepEqual(kinds(night({ items: { white_bed: 1 } }), { home, plan: null }), ['go_home', 'bed_here'])
})

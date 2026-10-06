import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { makeGoal, evaluate, needs } from '../src/goals.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const k = new Knowledge(md)
const world = { inventory: {}, blocks: {}, mobs: {}, table: null, unlocked: () => true }

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
  return evaluate(bot, { ...state, goal }, k, world).leaves.map((l) => l.kind)
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

test('夜を越す目標は、明け方（23000〜）から済み。夕方か夜に見ていなければ済みにならない', () => {
  const bot = night({ items: { white_bed: 1 } })
  const state = { home: null, plan: null }
  const goal = makeGoal({ predicate: 'through_night' }, bot, state, k) // 夜（14000）に立てた
  const at = (tod) => { bot.time.timeOfDay = tod; return evaluate(bot, { ...state, goal }, k, world) }
  assert.equal(at(14000).met, false)
  assert.equal(at(22000).met, false)
  assert.equal(at(23500).met, true) // 明け方
  assert.equal(at(1000).met, true) // 朝
  // 昼に立てた（夜を見ていない）目標は、昼のうちは済みにならない
  bot.time.timeOfDay = 6000
  const day = makeGoal({ predicate: 'through_night' }, bot, state, k)
  bot.time.timeOfDay = 23500
  assert.equal(evaluate(bot, { ...state, goal: day }, k, world).met, false)
  // 明け方に立てても、夜は見ていない
  assert.equal(makeGoal({ predicate: 'through_night' }, bot, state, k).sawNight, false)
})

test('深い地下では、夕方・夜の知らせを出さない（帰らせない）。地上では出す', () => {
  const dusk = (bot) => { bot.time.timeOfDay = 12500; bot.food = 20; bot.health = 20; bot.entities = {}; bot.pathfinder = { isMoving: () => false }; bot.findBlock = () => null; return bot }
  const st = { home: null, plan: null, formerHomes: [] }
  const under = dusk(night({ covered: true, y: 40 }))
  assert.ok(!needs(under, st).some((n) => /night/.test(n)), needs(under, st).join())
  under.time.timeOfDay = 18000
  assert.ok(!needs(under, st).some((n) => /night/.test(n)))
  const above = dusk(night())
  assert.ok(needs(above, st).some((n) => /night is coming/.test(n)))
  above.time.timeOfDay = 18000
  assert.ok(needs(above, st).some((n) => /night and you are outside/.test(n)))
})

test('暗い所では、松明が 0 本なら作りに戻るよう、少なければ足りなくなると知らせる。十分なら何も足さない', () => {
  const st = { home: null, plan: null, formerHomes: [] }
  const bot = (torches) => {
    const b = night({ covered: true, y: 40, items: torches ? { torch: torches } : {} })
    b.time.timeOfDay = 1000
    b.food = 20; b.health = 20; b.entities = {}; b.pathfinder = { isMoving: () => false }; b.findBlock = () => null
    return b
  }
  const dark = (t) => needs(bot(t), st).find((n) => n.startsWith('dark here'))
  assert.match(dark(0), /you carry no torch: turn back and make torches/)
  assert.match(dark(2), /only 2 torches left: place them every few blocks and make more/)
  assert.match(dark(1), /only 1 torch left/)
  assert.equal(dark(6), 'dark here (covered, no light near): hostile mobs can spawn around you')
})

// 家も寝床もない夜（docs/design/89）
test('家がなく縦穴も掘れない夜（砂浜、水のそば）は、立った所を囲んで朝を待つ手を出す', () => {
  const bot = night({ items: { dirt: 13 } })
  bot.entity.position = new Vec3(500, 64, 500) // 縦穴・野営の探索は同じ足元なら数秒前の答えを使う: ほかのテストと足元を分ける
  const inner = bot.blockAt
  bot.blockAt = (p) => (Math.floor(p.y) === 63 ? { name: 'sand', boundingBox: 'block', position: p } : { ...inner(p), position: p })
  const goal = makeGoal({ predicate: 'through_night' }, bot, { home: null, plan: null }, k)
  const r = evaluate(bot, { home: null, plan: null, goal }, k, world)
  const leaf = r.leaves.find((l) => l.kind === 'wall_in')
  assert.ok(leaf, r.leaves.map((l) => l.kind).join())
  assert.equal(leaf.need, 13)
  assert.ok(!r.leaves.some((l) => l.kind === 'dig_in'))
})

test('囲む材料がなく、近くに掘れる土もなければ、囲む手は出さず、足りない数を blocked に書く', () => {
  const bot = night()
  bot.entity.position = new Vec3(600, 64, 600)
  const inner = bot.blockAt
  bot.blockAt = (p) => (Math.floor(p.y) === 63 ? { name: 'sand', boundingBox: 'block', position: p } : { ...inner(p), position: p })
  const goal = makeGoal({ predicate: 'through_night' }, bot, { home: null, plan: null }, k)
  const r = evaluate(bot, { home: null, plan: null, goal }, k, world)
  assert.ok(!r.leaves.some((l) => l.kind === 'wall_in'))
  assert.ok(r.blocked.some((b) => /a camp takes 13 blocks: you carry 0 and there is dirt for 0 more/.test(b)), r.blocked.join('; '))
})

test('剣も斧もなければ、見えている木から作る手を足す。材料が見えていなければ出さない（暗がりに探しに歩かせない）', () => {
  const bot = night()
  const goal = makeGoal({ predicate: 'through_night' }, bot, { home: null, plan: null }, k)
  const state = { home: null, plan: null, goal }
  const none = evaluate(bot, state, k, world)
  assert.ok(!none.leaves.some((l) => l.also), 'no tree in view: no weapon leaves')
  const seen = evaluate(bot, state, k, { ...world, blocks: { oak_log: 5 } })
  assert.ok(seen.leaves.some((l) => l.kind === 'dig' && l.also === 'a weapon to fight with through the night'))
  assert.ok(!seen.leaves.some((l) => l.kind === 'explore'))
  // 剣を持っていれば作らない
  const armed = night({ items: { wooden_sword: 1 } })
  assert.ok(!evaluate(armed, { home: null, plan: null, goal: makeGoal({ predicate: 'through_night' }, armed, { home: null, plan: null }, k) }, k, { ...world, blocks: { oak_log: 5 } }).leaves.some((l) => l.also))
})

test('ドアも床もない家へは帰る手を出さない（落ちる）。理由を blocked に、野営の手は出す', () => {
  const home = { door: new Vec3(40, 64, 40), inside: new Vec3(40, 64, 39), outside: new Vec3(40, 64, 41), min: new Vec3(39, 64, 38), max: new Vec3(41, 64, 39), bed: null, breach: [] }
  const bot = night()
  const inner = bot.blockAt
  // 家のドアのマスは空気で、その下も空気（爆発で地面ごと飛んだ）
  bot.blockAt = (p) => (Math.floor(p.x) === 40 && Math.floor(p.z) === 40 && p.y >= 63 ? { name: 'air', boundingBox: 'empty', position: p } : { ...inner(p), position: p })
  const goal = makeGoal({ predicate: 'through_night' }, bot, { home, plan: null }, k)
  const r = evaluate(bot, { home, plan: null, goal }, k, world)
  assert.ok(!r.leaves.some((l) => l.kind === 'go_home'))
  assert.ok(r.blocked.some((b) => /door is gone and the floor under it too/.test(b)), r.blocked.join('; '))
  assert.ok(r.leaves.some((l) => l.kind === 'dig_in' || l.kind === 'wall_in'))
  // ドアだけがない（床はある）なら、帰ってよい（入ったあとに置き直す）
  const floor = night()
  const fi = floor.blockAt
  floor.blockAt = (p) => (Math.floor(p.x) === 40 && Math.floor(p.z) === 40 && p.y >= 64 ? { name: 'air', boundingBox: 'empty', position: p } : { ...fi(p), position: p })
  const g2 = makeGoal({ predicate: 'through_night' }, floor, { home, plan: null }, k)
  assert.ok(evaluate(floor, { home, plan: null, goal: g2 }, k, world).leaves.some((l) => l.kind === 'go_home'))
})

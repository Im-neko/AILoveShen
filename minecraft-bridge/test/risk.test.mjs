import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { assessRisk, inventoryValue, strength, atLeast, describeRisk } from '../src/risk.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const DAY = 1000
const NIGHT = 18000

function fakeBot ({ time = DAY, health = 20, food = 20, items = [], slots = [], mobs = [] } = {}) {
  const entity = { position: v(0, 70, 0) }
  const entities = { 0: entity }
  mobs.forEach(([name, dist], i) => { entities[i + 1] = { id: i + 1, type: 'hostile', name, position: v(dist, 70, 0) } })
  return {
    entity,
    entities,
    time: { timeOfDay: time },
    health,
    food,
    blockAt: () => ({ name: 'air', boundingBox: 'empty' }),
    inventory: { items: () => items.map(([name, count]) => ({ name, count })), slots }
  }
}

test('何も持たず元気なら、夜でも敵が近くにいても危なさは低い（失うものがない）', () => {
  const r = assessRisk(fakeBot({ time: NIGHT, mobs: [['zombie', 3]] }), {})
  assert.equal(r.stakes, 'nothing')
  assert.equal(r.level, 'low')
  assert.match(describeRisk(r), /bare hands, no armor; almost nothing to lose/)
})

test('値打ちのある持ち物があると、同じ状況でも危なさが上がり、先にチェストへ預けるよう添える', () => {
  const bot = fakeBot({ time: NIGHT, mobs: [['skeleton', 3], ['creeper', 5]], items: [['diamond_pickaxe', 1]] })
  const r = assessRisk(bot, {})
  assert.equal(r.stakes, 'valuable')
  assert.ok(atLeast(r, 'high'), r.level)
  assert.ok(r.advice.some((a) => /home chest/.test(a)))
  assert.ok(r.advice.some((a) => /creeper explodes/.test(a)))
})

test('体力が低いと危なさが上がる。低い体力と値打ちのある持ち物で極めて高い', () => {
  assert.equal(assessRisk(fakeBot({ health: 4, items: [['diamond_pickaxe', 1]] }), {}).level, 'high')
  assert.equal(assessRisk(fakeBot({ health: 4, items: [['diamond_pickaxe', 1]], mobs: [['zombie', 5]] }), {}).level, 'extreme')
  assert.equal(assessRisk(fakeBot({ health: 4 }), {}).level, 'medium')
  assert.equal(assessRisk(fakeBot({ health: 20 }), {}).level, 'low')
})

test('武器と防具があると敵を割り引く。着ていない防具（持ち物の欄）は数えない', () => {
  const mobs = [['skeleton', 3], ['zombie', 4]]
  // どちらも値打ちのある持ち物（ダイヤのツルハシ）があり、剣の有無だけが違う
  const bare = assessRisk(fakeBot({ mobs, items: [['diamond_pickaxe', 1]] }), {})
  const armed = assessRisk(fakeBot({ mobs, items: [['diamond_pickaxe', 1], ['iron_sword', 1]] }), {})
  assert.ok(armed.score < bare.score, `${armed.score} < ${bare.score}`)
  const carried = fakeBot({ slots: [{ name: 'iron_chestplate', slot: 20 }] })
  const worn = fakeBot({ slots: [{ name: 'iron_chestplate', slot: 6 }] })
  assert.equal(strength(carried).armor, 0)
  assert.equal(strength(worn).armor, 0.6)
})

test('持ち物の値打ち: 食べ物・松明・素材はほぼ 0、道具と防具は格で決まる', () => {
  assert.equal(inventoryValue(fakeBot({ items: [['dirt', 64], ['bread', 10], ['torch', 20], ['oak_log', 30]] })), 0)
  assert.equal(inventoryValue(fakeBot({ items: [['diamond_pickaxe', 1]] })), 15)
  assert.ok(inventoryValue(fakeBot({ items: [['iron_chestplate', 1]] })) > inventoryValue(fakeBot({ items: [['iron_boots', 1]] })))
})

test('遠くの敵は薄く数える。味方のモブ（敵対でない）は数えない', () => {
  const near = assessRisk(fakeBot({ mobs: [['skeleton', 3]], items: [['diamond_pickaxe', 1]] }), {})
  const far = assessRisk(fakeBot({ mobs: [['skeleton', 14]], items: [['diamond_pickaxe', 1]] }), {})
  const out = assessRisk(fakeBot({ mobs: [['skeleton', 30]], items: [['diamond_pickaxe', 1]] }), {})
  assert.ok(near.score > far.score && far.score > out.score, `${near.score} ${far.score} ${out.score}`)
  const friendly = fakeBot({ items: [['diamond_pickaxe', 1]] })
  friendly.entities[5] = { id: 5, type: 'passive', name: 'cow', position: v(2, 70, 0) }
  assert.equal(assessRisk(friendly, {}).score, 0)
})

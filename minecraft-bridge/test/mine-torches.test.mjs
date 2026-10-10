// 採掘場へは、残りを掘るぶんの松明を手に持って行く（2026-10-10: 松明はチェストにあるのに、手持ち数本で採掘場へ行こうとした）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { solve } from '../src/solver.mjs'
import { mineTorchesWanted, enoughToGo } from '../src/mine-torches.mjs'

const k = new Knowledge(minecraftData('1.21.4'))
const kinds = (r) => r.leaves.map((l) => `${l.kind}${l.item ? ':' + l.item : ''}`)
const mineWorld = (inventory = {}, extra = {}) => ({
  inventory: { stone_pickaxe: 1, wooden_pickaxe: 1, wooden_sword: 1, stick: 3, ...inventory }, blocks: {}, mobs: {}, table: { x: 0, y: 0, z: 0 }, unlocked: () => true,
  home: { name: 'home' }, buried: () => [], digDepth: 12, minePlan: () => ({ dig: 'mine_1' }), mine: null, mines: [], ...extra
})

test('必要な松明の数は、残りのセルの 1.5 倍を 6 セルごとに 1 本とし、予備 8、16〜64', () => {
  assert.equal(mineTorchesWanted(0), 16)
  assert.equal(mineTorchesWanted(54), 22) // 9 本 x 1.5 = 14 + 8
  assert.equal(mineTorchesWanted(307), 64) // 77 + 8 は上限で止まる
  assert.equal(mineTorchesWanted(Infinity), 64)
  assert.equal(enoughToGo(22), 11)
})

test('松明がチェストにたくさんあって手持ちが少ないなら、下りる前にチェストから出す（出す手が先で、掘る手は出ない）', () => {
  const r = solve(k, mineWorld({ torch: 5 }, { stored: { torch: 60 }, mineTorches: () => 22 }), [{ spec: 'raw_iron', count: 1 }])
  const w = r.leaves.find((l) => l.kind === 'withdraw')
  assert.ok(w && w.item === 'torch' && w.count === 17, JSON.stringify(r.leaves))
  assert.ok(!r.leaves.some((l) => l.kind === 'dig_mine'), JSON.stringify(kinds(r)))
})

test('必要な数を持っていれば、そのまま下りる。今までの数（8）しか求めない世界は変わらない', () => {
  const full = solve(k, mineWorld({ torch: 22 }, { mineTorches: () => 22 }), [{ spec: 'raw_iron', count: 1 }])
  assert.ok(full.leaves.some((l) => l.kind === 'dig_mine'))
  assert.ok(!full.leaves.some((l) => l.kind === 'withdraw'))
  const old = solve(k, mineWorld({ torch: 8 }, { stored: { torch: 60 } }), [{ spec: 'raw_iron', count: 1 }])
  assert.ok(old.leaves.some((l) => l.kind === 'dig_mine') && !old.leaves.some((l) => l.kind === 'withdraw'))
})

test('チェストの松明が足りなければ、あるぶんを出し、残りは作る材料（石炭）を掘りに行く。何も無くても、持っている数で下りる', () => {
  const r = solve(k, mineWorld({ torch: 9 }, { stored: { torch: 4 }, mineTorches: () => 22 }), [{ spec: 'raw_iron', count: 1 }])
  assert.ok(r.leaves.some((l) => l.kind === 'withdraw' && l.item === 'torch' && l.count === 4), JSON.stringify(r.leaves))
  const none = solve(k, mineWorld({ torch: 9 }, { mineTorches: () => 22 }), [{ spec: 'raw_iron', count: 1 }])
  assert.ok(none.leaves.some((l) => l.kind === 'dig_mine'), JSON.stringify(kinds(none)))
})

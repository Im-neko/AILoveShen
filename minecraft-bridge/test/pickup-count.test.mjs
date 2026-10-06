// 掘ったドロップを拾えたかは、増えた物の増え分で見る。総数の純増では、歩く間に使った物（足場の土、trail の松明）で
// 差し引かれて、拾えたのに「拾えなかった」と失敗した（2026-10-04 02:40: 鉄 +1、丸石 +1、松明 -2）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { gainedSince } from '../src/primitives.mjs'

const botWith = (counts) => ({ inventory: { items: () => Object.entries(counts).map(([name, count]) => ({ name, count })) } })

test('増えた物だけを数える: 使った物で差し引かない', () => {
  const baseline = { torch: 4, dirt: 10, stone_pickaxe: 1 }
  assert.equal(gainedSince(botWith({ torch: 2, dirt: 10, stone_pickaxe: 1, raw_iron: 1, cobblestone: 1 }), baseline), 2, '松明 2 本を使い、鉄と丸石を拾った')
  assert.equal(gainedSince(botWith({ torch: 4, dirt: 7, stone_pickaxe: 1, oak_log: 1 }), baseline), 1, '足場の土を 3 つ使い、原木を拾った')
})

test('何も増えていなければ 0（使っただけ・変化なし）', () => {
  assert.equal(gainedSince(botWith({ torch: 2, dirt: 10 }), { torch: 4, dirt: 10 }), 0)
  assert.equal(gainedSince(botWith({ torch: 4 }), { torch: 4 }), 0)
})

test('すでに持っている物が増えたのも数える', () => {
  assert.equal(gainedSince(botWith({ raw_iron: 3 }), { raw_iron: 1 }), 2)
})

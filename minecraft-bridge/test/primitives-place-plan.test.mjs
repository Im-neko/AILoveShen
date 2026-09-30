import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PRIMITIVES } from '../src/primitives.mjs'

const signal = new AbortController().signal

function once (results) {
  const original = PRIMITIVES.place_plan_once
  let i = 0
  PRIMITIVES.place_plan_once = async () => {
    const r = results[i++]
    if (r instanceof Error) throw r
    return r
  }
  return { calls: () => i, restore: () => { PRIMITIVES.place_plan_once = original } }
}

test('place_plan は count 個まで続けて置き、最後に置いた数を添える', async () => {
  const t = once(['placed planks (2/144)', 'placed planks (3/144)', 'placed planks (4/144)', 'placed planks (5/144)'])
  try {
    assert.equal(await PRIMITIVES.place_plan({}, {}, { count: 3 }, signal), 'placed planks (4/144) — 3 blocks placed in a row')
    assert.equal(t.calls(), 3)
  } finally { t.restore() }
})

test('place_plan は置く物がなくなったら止まる（完成した・最後の 1 個を置いた）', async () => {
  const done = once(['placed planks (143/144)', 'the plan is complete', 'unused'])
  try {
    assert.equal(await PRIMITIVES.place_plan({}, {}, { count: 10 }, signal), 'placed planks (143/144)')
    assert.equal(done.calls(), 2)
  } finally { done.restore() }
  const last = once(['placed planks (143/144)', 'placed door; the build hut is complete and the home now includes it', 'unused'])
  try {
    assert.match(await PRIMITIVES.place_plan({}, {}, { count: 10 }, signal), /complete/)
    assert.equal(last.calls(), 2)
  } finally { last.restore() }
})

test('place_plan は途中で置けなくなっても、置けた分を成功として返す。1 個も置けなければ失敗', async () => {
  const some = once(['placed planks (2/144)', 'placed planks (3/144)', new Error('you have no planks')])
  try {
    assert.match(await PRIMITIVES.place_plan({}, {}, { count: 10 }, signal), /stopped after 2 blocks: you have no planks/)
  } finally { some.restore() }
  const none = once([new Error('you have no planks')])
  try {
    await assert.rejects(PRIMITIVES.place_plan({}, {}, { count: 10 }, signal), /no planks/)
  } finally { none.restore() }
})

test('候補が指したブロック（planBlock）は 1 個だけ置く', async () => {
  const t = once(['placed door (9/144)', 'unused'])
  try {
    await PRIMITIVES.place_plan({}, {}, { planBlock: { block: 'door' }, count: 60 }, signal)
    assert.equal(t.calls(), 1)
  } finally { t.restore() }
})

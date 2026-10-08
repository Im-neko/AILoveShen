// 道具の呼び出しの入口（callTool）の契約（docs/design/90）: どの行動の道具も、同じ順序で同じ関門を通る。
// 順序は unknown → reflex → busy → 台帳（demolish だけが通り越す）→ 道具ごとの検査 → 夜の籠もり → 実行。
// 関門が断った呼び出しは、必ず { ok: false, refused: true, seconds: 0 } を返し、実行（run）を呼ばない。
// 道具を足したのに実行を書き忘れる（常に unknown tool と断られる）ことも、ここで捕まる。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { createTools, ACTION_TOOLS, QUERY_TOOLS } from '../src/tools.mjs'
import { noteRefusal } from '../src/refusals.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const md = minecraftData('1.21.4')
const knowledge = new Knowledge(md)

function setup () {
  const entity = { position: { x: 0, y: 70, z: 0 }, eyeHeight: 1.62 }
  const bot = {
    entity,
    entities: { 0: entity },
    registry: md,
    time: { timeOfDay: 1000 },
    health: 20,
    food: 20,
    blockAt: () => null,
    findBlock: () => null,
    findBlocks: () => [],
    world: { raycast: () => null },
    inventory: { items: () => [] }
  }
  const state = { home: null, formerHomes: [], plan: null, busy: false, reflex: false, memory: {}, unreachableDrops: new Set() }
  const runs = []
  const callTool = createTools({
    bot,
    state,
    knowledge,
    run: async (c, label) => { runs.push({ c, label }); return { ok: true, result: 'done', seconds: 0 } },
    candidates: () => [],
    check: () => []
  })
  return { callTool, state, runs }
}

const isRefusal = (r) => r.ok === false && r.refused === true && r.seconds === 0 && /^refused: /.test(r.result)

test('知らない道具は、反射や実行中より先に断る', async () => {
  const { callTool, state, runs } = setup()
  state.reflex = true
  const r = await callTool('no_such_tool')
  assert.ok(isRefusal(r))
  assert.match(r.result, /unknown tool no_such_tool/)
  assert.equal(runs.length, 0)
})

test('すべての行動の道具に実行がある（書き忘れると unknown tool と断られ続ける）', async () => {
  const { callTool, state } = setup()
  state.reflex = true // 反射の関門は実行の有無の確認より後: 実行があれば reflex で断られる
  for (const name of ACTION_TOOLS) {
    const r = await callTool(name, {})
    assert.match(r.result, /reflex/, `${name}: ${r.result}`)
  }
})

test('反射の間は、どの行動の道具も実行しない。反射は実行中より先に断る', async () => {
  const { callTool, state, runs } = setup()
  state.reflex = true
  state.busy = true
  for (const name of ACTION_TOOLS) {
    const r = await callTool(name, { x: 1, y: 70, z: 1 })
    assert.ok(isRefusal(r), `${name}: ${JSON.stringify(r)}`)
    assert.match(r.result, /the reflex is handling/, name)
  }
  assert.equal(runs.length, 0)
})

test('実行中は、どの行動の道具も実行しない', async () => {
  const { callTool, state, runs } = setup()
  state.busy = true
  for (const name of ACTION_TOOLS) {
    const r = await callTool(name, { x: 1, y: 70, z: 1 })
    assert.ok(isRefusal(r), `${name}: ${JSON.stringify(r)}`)
    assert.match(r.result, /another action is running/, name)
  }
  assert.equal(runs.length, 0)
})

test('台帳に載った手は、実行中より後に断る。demolish だけが台帳を通り越す', async (t) => {
  process.env.BRIDGE_GUARDS = 'standard' // 標準の断り（設計書 125）
  t.after(() => { delete process.env.BRIDGE_GUARDS })
  const { callTool, state, runs } = setup()
  const pos = { x: 5, y: 70, z: 5 }
  assert.ok(noteRefusal(state, 'dig', pos, 'will not dig stone: it holds back water'))

  state.busy = true
  assert.match((await callTool('dig', pos)).result, /another action is running/)
  state.busy = false

  const again = await callTool('dig', pos)
  assert.ok(isRefusal(again))
  assert.match(again.result, /was tried before/)
  assert.equal(runs.length, 0)

  // demolish は台帳を通り越して道具ごとの検査に進む（ここでは世界が空なので、別の理由で断られる）
  const bypass = await callTool('dig', { ...pos, demolish: true })
  assert.doesNotMatch(bypass.result, /was tried before/)
})

test('調べものの道具は、反射や実行中でも答え、実行（run）を呼ばず、実行中の印も立てない', async () => {
  const { callTool, state, runs } = setup()
  state.reflex = true
  state.busy = true
  for (const name of QUERY_TOOLS) {
    const r = await callTool(name, {})
    // 引数が足りなければ理由つきで断られてよい。ただし関門の理由（reflex / running）では断らない
    assert.doesNotMatch(JSON.stringify(r.result), /reflex is handling|another action is running/, name)
  }
  assert.equal(runs.length, 0)
  assert.equal(state.busy, true)
})

test('引数が知識と合わない呼び出しは、例外ではなく理由つきの断りになる（プレイを止めない）', async () => {
  const { callTool } = setup()
  const r = await callTool('craft', { item: 'definitely_not_an_item' })
  assert.ok(isRefusal(r))
})

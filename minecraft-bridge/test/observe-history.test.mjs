import { test } from 'node:test'
import assert from 'node:assert/strict'
import { HISTORY } from '../src/observe.mjs'

test('観測に載せる直近の行動は 8 件（Jev と各プロンプトが前の手の流れを見る）', () => {
  assert.equal(HISTORY, 8)
})

test('行動の記録は 100 件までで、古いものから捨てる', async () => {
  const { pushHistory } = await import('../src/observe.mjs')
  const state = { history: [] }
  for (let i = 0; i < 250; i++) pushHistory(state, { action: `a${i}` })
  assert.equal(state.history.length, 100)
  assert.equal(state.history[0].action, 'a150')
  assert.equal(state.history.at(-1).action, 'a249')
})

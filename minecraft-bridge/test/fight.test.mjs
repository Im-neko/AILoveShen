import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { fight } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

test('離れた相手を追う目標は一度だけ置く（置き直すと歩く途中の掘りが止まる）', async () => {
  const target = { id: 7, name: 'zombie', height: 1.95, position: new Vec3(10, 64, 0) }
  const goals = []
  let ticks = 0
  const controller = new AbortController()
  const bot = {
    entities: { 7: target },
    entity: { position: new Vec3(0, 64, 0) },
    pathfinder: {
      goal: null,
      setGoal (g) { this.goal = g; goals.push(g) }
    },
    waitForTicks: async () => { if (++ticks >= 5) controller.abort() }
  }
  await fight(bot, target, controller.signal)
  assert.equal(goals.filter((g) => g !== null).length, 1)
  assert.equal(goals.at(-1), null) // 終わったら止める
})

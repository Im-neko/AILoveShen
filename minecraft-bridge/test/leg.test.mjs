import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { PRIMITIVES, LEG } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

// 経路探索が、渡された目的地にすぐ着くボット（行き先を記録する）
function walker (x, z) {
  const bot = {
    entity: { position: new Vec3(x, 70, z) },
    blockAt: () => null, // 地形は読み込まれていない（水を渡る判定はしない）
    goals: [],
    pathfinder: {
      goto: async (goal) => { bot.goals.push(goal); bot.entity.position = new Vec3(goal.x, 70, goal.z) },
      setGoal: () => {}
    }
  }
  return bot
}

const { signal } = new AbortController()

test('遠い所へは 1 回の行動で、区間（LEG m）ごとに歩いて着く（時間では止めない）', async () => {
  const bot = walker(0, 0)
  const c = { target: 'sheep', pos: new Vec3(0, 70, 130) }
  assert.match(await PRIMITIVES.goto_memory(bot, {}, c, signal), /^arrived where sheep was seen/)
  // 48m、96m、最後の区間
  assert.deepEqual(bot.goals.map((g) => Math.round(g.z)), [LEG, 2 * LEG, 130])
})

test('道具の goto も着くまで歩く。止める合図が来たら区間の途中でも止まる', async () => {
  const bot = walker(0, 0)
  assert.match(await PRIMITIVES.goto_pos(bot, {}, { pos: new Vec3(0, 70, 100), range: 1 }, signal), /^arrived near 0,70,100/)
  const controller = new AbortController()
  const slow = walker(0, 0)
  const goto = slow.pathfinder.goto
  slow.pathfinder.goto = async (goal) => { await goto(goal); controller.abort(new Error('stopped: it is dusk now')) }
  await assert.rejects(PRIMITIVES.goto_pos(slow, {}, { pos: new Vec3(0, 70, 300), range: 1 }, controller.signal), /dusk/)
  assert.equal(slow.goals.length, 1)
})

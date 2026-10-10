// 夕方の待ちは、ベッドが使える時刻ちょうどまで（10 秒きざみで待って、時刻を過ぎて余計に待たない）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg

function fakeBot (tod) {
  const waited = []
  const bot = {
    time: { timeOfDay: tod },
    entity: { yaw: 0, position: new Vec3(0, 64, 0) },
    lookAt: async () => {},
    look: async () => {},
    waitForTicks: async (n) => { waited.push(n); bot.time.timeOfDay += n }
  }
  return { bot, waited }
}

test('until があれば、その時刻まで待つ。なければ 200 tick 待つ', async () => {
  const signal = new AbortController().signal
  const total = (waited) => waited.reduce((a, b) => a + b, 0)
  const { bot, waited } = fakeBot(12400)
  await PRIMITIVES.wait(bot, {}, { until: 12542 }, signal)
  assert.ok(bot.time.timeOfDay >= 12542 && total(waited) <= 250, `${bot.time.timeOfDay}`)

  const far = fakeBot(12000)
  await PRIMITIVES.wait(far.bot, {}, { until: 12542 }, signal)
  assert.equal(total(far.waited), 200, '遠ければ 10 秒で一度戻る（次の手で選び直す）')

  const plain = fakeBot(12000)
  await PRIMITIVES.wait(plain.bot, {}, {}, signal)
  assert.equal(total(plain.waited), 200)
})

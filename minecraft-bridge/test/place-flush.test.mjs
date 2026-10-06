// 置くマスの縁に張り付いた位置からは、サーバーに「置けない」と断られる（壁を壊して出たあとの補修が、
// 同じマス (61,77,-329) で x=62.3 から 3 回断られた）。置く前に、縁から離れる
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { flushWith, backOffFrom } from '../src/build.mjs'

const { Vec3 } = vec3Pkg
const cell = new Vec3(61, 77, -329)

test('flushWith: 縁に触れている位置だけが真。中・離れている・高さが違うときは偽', () => {
  assert.equal(flushWith(new Vec3(62.3, 77, -328.49), cell), true, '記録の位置（x=62.3）')
  assert.equal(flushWith(new Vec3(60.7, 77, -328.5), cell), true, '反対側の縁')
  assert.equal(flushWith(new Vec3(62.5, 77, -328.5), cell), false, '自分のマスの真ん中')
  assert.equal(flushWith(new Vec3(61.5, 77, -328.5), cell), false, 'マスの中は別の話（縁ではない）')
  assert.equal(flushWith(new Vec3(62.3, 79, -328.5), cell), false, '頭の上を通り過ぎている')
  assert.equal(flushWith(new Vec3(62.3, 74, -328.5), cell), false, '足の下')
})

test('backOffFrom: 縁に張り付いていれば自分のマスの真ん中へ寄り、前進キーを離す', async () => {
  const pressed = []
  const bot = {
    entity: { position: new Vec3(62.3, 77, -328.49) },
    lookAt: async () => {},
    setControlState (key, on) { pressed.push([key, on]) },
    waitForTicks: async () => { bot.entity.position.x += 0.04 * Math.sign(62.5 - bot.entity.position.x) }
  }
  await backOffFrom(bot, cell)
  assert.ok(Math.abs(bot.entity.position.x - 62.5) <= 0.12, `x=${bot.entity.position.x}`)
  assert.deepEqual(pressed[0], ['forward', true])
  assert.deepEqual(pressed.at(-1), ['forward', false])
})

test('backOffFrom: 張り付いていなければ何もしない', async () => {
  const bot = { entity: { position: new Vec3(62.5, 77, -328.5) }, lookAt: async () => assert.fail('動いた'), setControlState: () => assert.fail('動いた'), waitForTicks: async () => {} }
  await backOffFrom(bot, cell)
})

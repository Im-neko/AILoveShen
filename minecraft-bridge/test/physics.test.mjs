import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'

// prismarine-physics は patches/ の差分で、Minecraft と同じ許容誤差（1e-7）で衝突を判定する
const require = createRequire(import.meta.url)
const { Physics, PlayerState } = require('prismarine-physics')
const Block = require('prismarine-block')('1.21.4')
const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

const block = (name, p) => Object.assign(Block.fromStateId(md.blocksByName[name].defaultState, 0), { position: p })
// 地面は y 65、(-2, 65〜67, -3) にトウヒの幹
const world = {
  getBlock (p) {
    const f = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    if (f.y < 65) return block('grass_block', f)
    if (f.x === -2 && f.z === -3 && f.y <= 67) return block('spruce_log', f)
    return block('air', f)
  }
}

test('幹の面にぴったり接していても、幹の中へは歩き込まない（town4: サーバーが位置を戻し続けた）', () => {
  const physics = Physics(md, world)
  // サーバーが戻した位置そのもの: 箱の端は -2.3 + 0.3 = -1.9999999999999998 で、幹（x = -2）に 2e-16 重なる
  const bot = {
    version: '1.21.4',
    entity: { position: new Vec3(-2.3, 65, -1.8136252884955801), velocity: new Vec3(0, 0, 0), onGround: true, isInWater: false, isInLava: false, isInWeb: false, isCollidedHorizontally: false, isCollidedVertically: false, elytraFlying: false, yaw: 4.338864259060372, pitch: 0, effects: {}, attributes: {} },
    jumpTicks: 0,
    jumpQueued: false,
    fireworkRocketDuration: 0,
    inventory: { slots: [] },
    game: { gameMode: 'survival' }
  }
  for (let i = 0; i < 10; i++) {
    const s = new PlayerState(bot, { forward: true, back: false, left: false, right: false, jump: false, sprint: false, sneak: false })
    physics.simulatePlayer(s, world)
    s.apply(bot)
    const p = bot.entity.position
    const inTrunk = p.x + 0.3 > -2 + 1e-7 && p.z - 0.3 < -2 - 1e-7
    assert.ok(!inTrunk, `tick ${i}: ${p}`)
  }
})

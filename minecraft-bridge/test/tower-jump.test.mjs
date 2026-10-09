// 足場積み（経路探索の真上置き）: patches/mineflayer-pathfinder の差分で、体が置くマスの上に出てから置く
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'

const require = createRequire(import.meta.url)
const { Physics, PlayerState } = require('prismarine-physics')
const Block = require('prismarine-block')('1.21.4')
const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')

const GROUND = 75 // 足元のブロックの上面（立っている足の高さ）
const world = {
  getBlock (p) {
    const f = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    return Object.assign(Block.fromStateId(md.blocksByName[f.y < GROUND ? 'dirt' : 'air'].defaultState, 0), { position: f })
  }
}

// 土台の高さ（足元のブロックの y）から、置いてよい足の高さ。経路探索の条件と同じ式
const canPlace = (refY, feetY) => refY + 2 <= feetY

test('パッチが入っている: 真上置きは、足が置くマスの上端に届いてから置く', () => {
  const src = readFileSync(new URL('../node_modules/mineflayer-pathfinder/index.js', import.meta.url), 'utf8')
  assert.ok(src.includes('canPlace = placingBlock.y + 2 <= bot.entity.position.y'))
  assert.ok(!src.includes('canPlace = placingBlock.y + 1 < bot.entity.position.y'))
})

test('その条件は、立った状態からの 1 回の跳びで満たせる（頂点の前後に置ける間がある）。浮いた直後は満たさない', () => {
  const physics = Physics(md, world)
  const bot = {
    version: '1.21.4',
    entity: { position: new Vec3(3.5, GROUND, -292.5), velocity: new Vec3(0, 0, 0), onGround: true, isInWater: false, isInLava: false, isInWeb: false, isCollidedHorizontally: false, isCollidedVertically: false, elytraFlying: false, yaw: 0, pitch: 0, effects: [] },
    jumpTicks: 0,
    jumpQueued: false,
    fireworkRocketDuration: 0,
    inventory: { slots: [] },
    game: { gameMode: 'survival' }
  }
  const refY = GROUND - 1
  let early = 0 // 旧い条件（浮いたらすぐ）だけが満たす tick
  let ok = 0
  for (let i = 0; i < 20; i++) {
    const s = new PlayerState(bot, { forward: false, back: false, left: false, right: false, jump: true, sprint: false, sneak: false })
    physics.simulatePlayer(s, world)
    s.apply(bot)
    const y = bot.entity.position.y
    if (refY + 1 < y && !canPlace(refY, y)) early++
    if (canPlace(refY, y)) ok++
  }
  assert.ok(early >= 3, `浮いてから届くまでに ${early} tick`)
  assert.ok(ok >= 2, `置ける tick が ${ok}`)
})

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import vec3Pkg from 'vec3'
import { damageCause, installDamageLog } from '../src/damage-log.mjs'

const { Vec3 } = vec3Pkg

function fakeBot ({ feet = 'air', food = 20, mobs = [], oxygen = 20, effects = {}, flags = undefined } = {}) {
  const bot = new EventEmitter()
  const entities = {}
  mobs.forEach((m, i) => { entities[i + 1] = { id: i + 1, type: 'hostile', name: m.name, position: new Vec3(m.d, 70, 0) } })
  return Object.assign(bot, {
    health: 20, food, oxygenLevel: oxygen,
    entity: { position: new Vec3(0.5, 70, 0.5), velocity: { y: 0 }, effects, metadata: flags === undefined ? [] : [flags] },
    registry: { effectsArray: [{ id: 19, name: 'Poison' }, { id: 20, name: 'Wither' }, { id: 1, name: 'Speed' }] },
    entities,
    blockAt: () => ({ name: feet })
  })
}

test('水の中で頭が沈んで約 14 秒たったら、bot.oxygenLevel が気泡の尺度でなくても（300 前後）溺れと推す', () => {
  const bot = fakeBot({ feet: 'water', oxygen: 307 })
  bot.entity.isInWater = true
  const real = Date.now
  const base = real()
  try {
    Date.now = () => base
    assert.match(damageCause(bot, 0), /^unknown \(in water/) // 入ったばかり
    let last = ''
    for (let k = 1; k <= 15; k++) { Date.now = () => base + k * 1000; last = damageCause(bot, 0) }
    assert.match(last, /^drowning/)
  } finally {
    Date.now = real
  }
})

test('原因は、溶岩・飢え・溺れ・落下・近くの敵対モブの順に推す。どれでもなければ不明', () => {
  assert.equal(damageCause(fakeBot({ feet: 'lava' }), 0), 'lava')
  assert.equal(damageCause(fakeBot({ food: 0 }), 0), 'starving (food 0)')
  assert.equal(damageCause(fakeBot({ oxygen: 0 }), 0), 'drowning')
  assert.match(damageCause(fakeBot(), 25), /^fall/)
  assert.match(damageCause(fakeBot({ mobs: [{ name: 'zombie', d: 3 }, { name: 'skeleton', d: 5 }] }), 0), /^zombie 2\.\d+m$|^zombie 3\.\d+m$|^zombie/)
  assert.match(damageCause(fakeBot({ mobs: [{ name: 'zombie', d: 20 }] }), 0), /^unknown/)
})

test('体力が減ったときだけ 1 行出す（回復と小さな変化は出さない）', () => {
  const bot = fakeBot({ mobs: [{ name: 'creeper', d: 2 }] })
  installDamageLog(bot, () => 'dig the mine')
  const lines = []
  const log = console.log
  console.log = (s) => lines.push(s)
  try {
    bot.health = 20; bot.emit('health') // 最初は基準
    bot.health = 16; bot.emit('health')
    bot.health = 17; bot.emit('health') // 回復
    bot.health = 16.8; bot.emit('health') // 小さい
  } finally { console.log = log }
  assert.equal(lines.length, 1)
  assert.match(lines[0], /^\[damage\] -4\.0 -> 16\.0\/20 food 20: creeper .* at 0,70,0 while dig the mine$/)
})

test('1 件ごとに onDamage へ { at, lost, health, cause, what } を渡す（死んだときの様子に使う）', () => {
  const bot = fakeBot({ mobs: [{ name: 'zombie', d: 2 }] })
  const got = []
  installDamageLog(bot, () => 'level the ground', (d) => got.push(d))
  const log = console.log
  console.log = () => {}
  try {
    bot.health = 20; bot.emit('health')
    bot.health = 17.5; bot.emit('health')
    bot.health = 17.6; bot.emit('health') // 回復
  } finally { console.log = log }
  assert.equal(got.length, 1)
  assert.deepEqual([got[0].lost, got[0].health, got[0].what], [2.5, 17.5, 'level the ground'])
  assert.match(got[0].cause, /^zombie /)
  assert.equal(typeof got[0].at, 'number')
})

test('毒・衰弱は、敵が離れたあとも続くので敵より先に推す。燃えているときもそう', () => {
  const poisoned = fakeBot({ effects: { 19: { id: 19, amplifier: 0, duration: 240 } }, mobs: [{ name: 'cave_spider', d: 20 }] })
  assert.equal(damageCause(poisoned, 0), 'poison (level 1, 12 s left)')
  assert.match(damageCause(fakeBot({ effects: { 20: { id: 20, amplifier: 1, duration: 100 } } }), 0), /^wither \(level 2, 5 s left\)/)
  assert.equal(damageCause(fakeBot({ effects: { 1: { id: 1, amplifier: 0, duration: 400 } } }), 0).startsWith('unknown'), true, '害のない状態異常は数えない')
  assert.equal(damageCause(fakeBot({ flags: 0x01 }), 0), 'burning (on fire)')
  assert.equal(damageCause(fakeBot({ flags: 0x02 }), 0).startsWith('unknown'), true, '燃焼以外のフラグ（スニークなど）は数えない')
  assert.equal(damageCause(fakeBot({ feet: 'lava', flags: 0x01 }), 0), 'lava', '溶岩の上ならそちらが先')
})

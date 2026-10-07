// 死んだときの様子（docs/design/105）: 受けたダメージを直近だけ覚え、死んだ瞬間に 1 件にまとめる
import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { noteDamage, deathReport, kindOf } from '../src/death-report.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const home = { door: new Vec3(0, 70, 0), inside: new Vec3(0, 70, -2), min: new Vec3(-2, 70, -4), max: new Vec3(2, 70, -1) }

// 屋根のない地面の上、座標 (x, 70, 0) に立つ。timeOfDay 18000 は夜
function bot (x = 48.5, timeOfDay = 18000) {
  return {
    registry: md,
    time: { timeOfDay },
    entity: { position: new Vec3(x, 70, 0.5) },
    blockAt: (p) => (p.y < 70 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' })
  }
}

test('原因の種類: 敵の名前、不明、落下は括弧の前まで', () => {
  assert.equal(kindOf('zombie 2.1m'), 'zombie')
  assert.equal(kindOf('unknown (no hostile within 6 m: suffocation, an unseen shot, an explosion?)'), 'unknown')
  assert.equal(kindOf('fall (3.2 blocks per second)'), 'fall')
  assert.equal(kindOf('drowning'), 'drowning')
})

test('死んだ瞬間に、時間帯・場所・家からの距離・受けたダメージの合計・していたことをまとめる', () => {
  const state = { home }
  const t = 1_000_000
  noteDamage(state, { at: t, lost: 2.5, health: 17.5, cause: 'zombie 2.1m', what: 'level the ground' })
  noteDamage(state, { at: t + 5000, lost: 2.5, health: 15, cause: 'zombie 1.4m', what: 'the reflex' })
  noteDamage(state, { at: t + 20000, lost: 3, health: 12, cause: 'skeleton 2.8m', what: 'the reflex' })
  const r = deathReport(bot(), state, t + 30000)
  assert.equal(r.phase, 'night')
  assert.equal(r.place, 'outside')
  assert.equal(r.from_home_m, 49)
  assert.equal(r.seconds, 30)
  assert.equal(r.health_before, 20)
  assert.deepEqual(r.damage, { zombie: 5, skeleton: 3 })
  assert.equal(r.hits, 3)
  assert.deepEqual(r.doing, ['level the ground', 'the reflex'])
})

test('90 秒より前のダメージは数えない。家の中なら place は home', () => {
  const state = { home }
  const t = 1_000_000
  noteDamage(state, { at: t, lost: 3, health: 17, cause: 'zombie 2.0m', what: '' })
  noteDamage(state, { at: t + 100000, lost: 2, health: 15, cause: 'creeper 1.0m', what: '' })
  assert.deepEqual(state.recentDamage.map((d) => d.cause), ['creeper 1.0m'], '覚えるときに古いものを捨てる')
  const r = deathReport({ ...bot(0), blockAt: (p) => ({ name: 'oak_planks', boundingBox: p.y > 70 ? 'block' : 'empty' }) }, state, t + 100500)
  assert.deepEqual(r.damage, { creeper: 2 })
  assert.equal(deathReport(bot(), { home }, t).hits, 0, 'ダメージがなければ空')
})

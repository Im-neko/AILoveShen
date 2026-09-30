import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { EventEmitter } from 'node:events'
import vec3Pkg from 'vec3'
import { snapshot, where, record, cellChar, pathOf } from '../src/stuck-report.mjs'
import { walkTo } from '../src/move.mjs'

const { Vec3 } = vec3Pkg

// 池: x < 5 は y 63..64 が水（床は 62 まで石）、x >= 5 は y 64 まで石の岸
const pond = (p) => {
  if (p.y <= 62) return { name: 'stone', boundingBox: 'block' }
  if (p.x >= 5) return p.y <= 64 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }
  return p.y <= 64 ? { name: 'water', boundingBox: 'empty' } : { name: 'air', boundingBox: 'empty' }
}

function botIn (at, inWater = true) {
  return { entity: { position: at, isInWater: inWater, onGround: false }, blockAt: (p) => ({ ...pond(p), position: p }) }
}

test('セルは 1 文字になる（水・溶岩・固い・それ以外・空気・未読み込み）', () => {
  assert.deepEqual(
    [{ name: 'water' }, { name: 'lava' }, { name: 'stone', boundingBox: 'block' }, { name: 'oak_fence', boundingBox: 'o' }, { name: 'air', boundingBox: 'empty' }, null].map(cellChar),
    ['~', 'L', '#', 'o', '.', '?']
  )
})

test('岸の手前の水の中の様子を、位置・水の中か・周りの 4 層・経路の次の手として残す', () => {
  const snap = snapshot(botIn(new Vec3(3.5, 64.2, 0.5)), { x: 8, y: 65, z: 0, constructor: { name: 'GoalNear' } }, {
    status: 'success',
    path: [{ x: 4, y: 64, z: 0, toBreak: [], toPlace: [] }, { x: 5, y: 65, z: 0, toBreak: [{}], toPlace: [] }, { x: 6, y: 65, z: 0 }, { x: 7, y: 65, z: 0 }]
  })
  assert.deepEqual(snap.at, [3.5, 64.2, 0.5])
  assert.equal(snap.in_water, true)
  assert.equal(snap.feet, 'water')
  assert.equal(snap.below, 'water')
  assert.equal(where(snap), '3.5,64.2,0.5 in water')
  assert.deepEqual(snap.grid.map((l) => l.dy), [2, 1, 0, -1])
  // 足元の高さ（dy 0、y=64）: 自分は @（x=3）、東の x=4 は水、x=5 からが岸
  assert.deepEqual(snap.grid[2].rows[2], '~~@~#')
  assert.deepEqual(snap.grid[0].rows[2], '.....')
  assert.deepEqual(snap.path.next.map((n) => [n.x, n.y, n.z, n.break]), [[4, 64, 0, 0], [5, 65, 0, 1], [6, 65, 0, 0]])
  assert.equal(snap.path.length, 4)
})

test('経路がまだなければ null、陸ならその位置だけ', () => {
  assert.equal(pathOf(null), null)
  const snap = snapshot(botIn(new Vec3(8.5, 65, 0.5), false), 'far', null)
  assert.equal(where(snap), '8.5,65,0.5')
  assert.equal(snap.path, null)
})

test('記録は 1 行の JSON で追記する', () => {
  const file = path.join(mkdtempSync(path.join(os.tmpdir(), 'stuck-')), 'a', 'stuck-walks.jsonl')
  const snap = snapshot(botIn(new Vec3(3.5, 64.2, 0.5)), 'far', null)
  record(snap, new Date('2026-09-30T00:00:00Z'), file)
  record(snap, new Date('2026-09-30T00:00:01Z'), file)
  const lines = readFileSync(file, 'utf8').trim().split('\n').map((l) => JSON.parse(l))
  assert.equal(lines.length, 2)
  assert.equal(lines[0].time, '2026-09-30T00:00:00.000Z')
  assert.equal(lines[1].in_water, true)
})

test('動けなくなった失敗の文に、位置と水の中かを添える', async () => {
  const bot = new EventEmitter()
  Object.assign(bot, botIn(new Vec3(3.5, 64.2, 0.5)), {
    look: async () => {},
    setControlState: () => {},
    clearControlStates: () => {},
    waitForTicks: async () => {},
    pathfinder: {
      movements: { allowParkour: true, allowSprinting: true },
      setMovements () {},
      setGoal (g) { if (g === null && this.reject) { const r = this.reject; this.reject = null; r(new Error('GoalChanged')) } },
      goto () { return new Promise((resolve, reject) => { this.reject = reject }) }
    }
  })
  process.env.STUCK_WALKS_FILE = path.join(mkdtempSync(path.join(os.tmpdir(), 'stuck-')), 'w.jsonl')
  await assert.rejects(walkTo(bot, 'far', new AbortController().signal, 30), /stuck: did not move for 0\.03s while walking at 3\.5,64\.2,0\.5 in water \(tried: jumped out/)
  const lines = readFileSync(process.env.STUCK_WALKS_FILE, 'utf8').trim().split('\n')
  assert.equal(lines.length, 5) // 最初の詰まり、対処のたびの詰まり 3 回、下がる途中の詰まり
  assert.equal(JSON.parse(lines[0]).grid[2].rows[2], '~~@~#')
})

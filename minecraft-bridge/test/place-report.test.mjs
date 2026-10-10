import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import vec3Pkg from 'vec3'
import { installPlaceReport, callerOf } from '../src/place-report.mjs'

const { Vec3 } = vec3Pkg

function fakeBot (placeBlock) {
  return {
    placeBlock,
    entity: { position: new Vec3(0.5, 70, 0.5), eyeHeight: 1.62, onGround: true, isInWater: false },
    entities: {},
    heldItem: { name: 'dirt' },
    blockAt: (p) => ({ name: p.y < 70 ? 'stone' : 'air', position: p })
  }
}

test('サーバーに断られた設置は、どこから呼ばれても記録して、同じエラーを投げ直す', async () => {
  const file = path.join(mkdtempSync(path.join(tmpdir(), 'place-')), 'refusals.jsonl')
  process.env.PLACE_REFUSALS_FILE = file
  try {
    const bot = fakeBot(async () => { throw new Error('Server refused to place dirt at (0, 70, 0): the block is still air') })
    installPlaceReport(bot, file)
    await assert.rejects(bot.placeBlock({ position: new Vec3(0, 69, 0), name: 'stone' }, new Vec3(0, 1, 0)), /Server refused to place dirt/)
    const rows = readFileSync(file, 'utf8').trim().split('\n').map((l) => JSON.parse(l))
    assert.equal(rows.length, 1)
    assert.deepEqual(rows[0].dest, { x: 0, y: 70, z: 0 })
    assert.equal(rows[0].against.name, 'stone')
    assert.equal(rows[0].bot.standing_in_dest, true)
  } finally {
    delete process.env.PLACE_REFUSALS_FILE
  }
})

test('置けたときと、ほかの失敗は記録しない', async () => {
  const file = path.join(mkdtempSync(path.join(tmpdir(), 'place-')), 'refusals.jsonl')
  const ok = fakeBot(async () => 'placed')
  installPlaceReport(ok, file)
  assert.equal(await ok.placeBlock({ position: new Vec3(0, 69, 0) }, new Vec3(0, 1, 0)), 'placed')
  const bad = fakeBot(async () => { throw new Error('must be holding an item to place') })
  installPlaceReport(bad, file)
  await assert.rejects(bad.placeBlock({ position: new Vec3(0, 69, 0) }, new Vec3(0, 1, 0)), /holding an item/)
  assert.equal(existsSync(file), false)
})

test('呼び出し元の印: 経路探索の足場積みか、ブリッジの関数か', () => {
  const pathfinder = 'Error\n    at Bot.placeBlock (file:///x/src/place-report.mjs:60:20)\n    at file:///x/node_modules/mineflayer-pathfinder/index.js:558:17'
  assert.equal(callerOf(pathfinder), 'pathfinder')
  const mine = 'Error\n    at Bot.placeBlock (file:///x/src/place-report.mjs:60:20)\n    at jumpAndPlace (file:///x/src/home.mjs:414:11)\n    at async Object.climb_out (file:///x/src/primitives.mjs:1326:9)'
  assert.equal(callerOf(mine), 'jumpAndPlace')
  assert.equal(callerOf('Error'), null)
})

test('timingOf: 呼んだ時・送った時の高さ（置く先のセルの下端から）と、直近の tick の高さを残す', async () => {
  const { timingOf } = await import('../src/place-report.mjs')
  const dest = new Vec3(5, 79, 5)
  const t = timingOf(dest, { t: 1000, y: 79.42 }, { t: 1060, y: 80.1666 }, [{ t: 900, y: 79.42 }, { t: 1000, y: 79.7532 }, { t: 1050, y: 80.0013 }], 1160)
  assert.deepEqual(t, { call_y: 0.42, send_y: 1.17, ms_call_to_send: 60, ms_send_to_refusal: 100, trail: [[-260, 0.42], [-160, 0.75], [-110, 1]] })
  assert.equal(timingOf(dest, { t: 1000, y: 79 }, null, [], 1100).send_y, null)
})

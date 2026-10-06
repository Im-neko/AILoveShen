import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import vec3Pkg from 'vec3'
import { installPlaceReport } from '../src/place-report.mjs'

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

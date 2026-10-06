import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { BuildPlan } from '../src/build.mjs'
import { planBlockToPlace } from '../src/primitives.mjs'
import { doorGone, homeEntryBlocked, openDoorOrBreak } from '../src/home.mjs'
import { noteRefusal, refusalAt, isFutile } from '../src/refusals.mjs'

const { Vec3 } = vec3Pkg

// 3x3 の家（壁 8 マスのうち 1 つがドア）。原点 (10,70,10)、ドアは (11,70,10)
function plan () {
  const blocks = []
  for (let x = 0; x < 3; x++) for (let z = 0; z < 3; z++) if (x !== 1 || z !== 1) blocks.push({ x, y: 0, z, block: x === 1 && z === 0 ? 'door' : 'planks' })
  const p = new BuildPlan({ blocks, width: 3, depth: 3, height: 2 })
  p.origin = new Vec3(10, 70, 10)
  return p
}
const SOLID = { name: 'stone', boundingBox: 'block' }
const AIR = { name: 'air', boundingBox: 'empty' }
const PLANKS = { name: 'oak_planks', boundingBox: 'block' }
const botOf = (cells) => ({ blockAt: (p) => cells[`${p.x},${p.y},${p.z}`] ?? (p.y < 70 ? SOLID : AIR) })
const door = { x: 11, y: 70, z: 10 }

test('床が抜けたドアを先に指されたら、足元の土から置く（pending の先頭）', () => {
  const p = plan()
  const doorBlock = p.blocks.find((b) => b.block === 'door')
  const bot = botOf({ '11,69,10': AIR }) // ドアの下だけ抜けている
  const b = planBlockToPlace(bot, p, doorBlock)
  assert.equal(b.foundation, true)
  assert.equal(b.block, 'dirt')
  assert.equal(b.y, -1)
})

test('床があれば、指されたドアをそのまま置く。ドアが置けていれば pending の先頭', () => {
  const p = plan()
  const doorBlock = p.blocks.find((b) => b.block === 'door')
  assert.equal(planBlockToPlace(botOf({}), p, doorBlock), doorBlock)
  const placed = botOf({ '11,70,10': { name: 'oak_door', boundingBox: 'empty', getProperties: () => ({ facing: 'north' }) } })
  assert.notEqual(planBlockToPlace(placed, p, doorBlock), doorBlock)
})

test('家のドアのマスがドアでなければ doorGone（読み込まれていなければ偽）。床もなければ入るのを断る理由が出る', () => {
  const home = { door: new Vec3(door.x, door.y, door.z) }
  assert.equal(doorGone(botOf({ '11,70,10': { name: 'oak_door', boundingBox: 'empty' } }), home), false)
  assert.equal(doorGone(botOf({}), home), true)
  assert.equal(doorGone({ blockAt: () => null }, home), false)
  assert.equal(homeEntryBlocked(botOf({}), home), null, '床はある: 入ってから置き直せる')
  assert.match(homeEntryBlocked(botOf({ '11,69,10': AIR }), home), /door is gone and the floor under it too/)
  assert.equal(homeEntryBlocked(botOf({ '11,70,10': { name: 'oak_door', boundingBox: 'empty' }, '11,69,10': AIR }), home), null)
})

test('ドアがもうなければ、開けずにそのまま入れる（「no door at」で落ちない）', async () => {
  const home = { door: new Vec3(door.x, door.y, door.z) }
  assert.equal(await openDoorOrBreak(botOf({}), home), true)
})

test('台帳が断った手（was tried before）は載せ直さない: 期限が延び続けず、理由が入れ子にならない', () => {
  const state = {}
  const p = new Vec3(45, 68, -199)
  assert.equal(noteRefusal(state, 'place', p, 'refused: will not place at 45,68,-199: it is part of the current home', 1000), true)
  const at = refusalAt(state, 'place', p, 1500)
  assert.ok(at)
  const until = at.until
  assert.equal(isFutile('refused: place at 45,68,-199 was tried before and will not work: will not place at 45,68,-199: it is part'), true, '入れ子の文は、台帳が自分で拾っていた')
  assert.equal(noteRefusal(state, 'place', p, 'refused: place at 45,68,-199 was tried before and will not work: will not place at 45,68,-199: it is part', 2000), false)
  assert.equal(refusalAt(state, 'place', p, 2500).until, until)
})

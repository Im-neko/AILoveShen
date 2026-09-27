import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import { validateCheck, runCheck, registerCheck, checksList, CheckError } from '../src/conditions.mjs'
import { checkConditions, makeGoal } from '../src/goals.mjs'
import { Knowledge } from '../src/knowledge.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')

// 地面は y 63 まで土（草は 63）。blocks: { 'x,y,z': name } で上書き
function fakeBot (blocks = {}, extra = {}) {
  const set = new Map(Object.entries(blocks))
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const name = set.get(`${q.x},${q.y},${q.z}`) ?? (q.y < 63 ? 'dirt' : q.y === 63 ? 'grass_block' : 'air')
    const b = Block.fromProperties(name, {}, 0)
    b.position = q
    return b
  }
  return {
    registry: md,
    blockAt,
    findBlocks: ({ matching }) => [...set].filter(([, n]) => matching.includes(md.blocksByName[n]?.id)).map(([k]) => new Vec3(...k.split(',').map(Number))),
    inventory: { items: () => [{ name: 'poppy', count: 2, type: md.itemsByName.poppy.id }] },
    entity: { position: new Vec3(0.5, 64, 0.5) },
    entities: {},
    time: { timeOfDay: 1000, age: 30000 },
    ...extra
  }
}

// 家の前（0,64,0 から東へ 4 マス）に花が 4 本
const FLOWERS = `
function check(w) {
  let n = 0
  for (let x = 0; x < 4; x++) if (w.block(x, 64, 0)?.name === 'poppy') n++
  return { met: n >= 4, remaining: 4 - n, lines: ['poppies in front of the door: ' + n + '/4'] }
}`

test('判定は世界を読んで met / remaining / lines を返す', () => {
  const bot = fakeBot({ '0,64,0': 'poppy', '1,64,0': 'poppy' })
  const r = runCheck(bot, {}, { name: 'flowers', code: FLOWERS })
  assert.deepEqual(r, { met: false, remaining: 2, lines: ['poppies in front of the door: 2/4'] })
  const done = fakeBot(Object.fromEntries([0, 1, 2, 3].map((x) => [`${x},64,0`, 'poppy'])))
  assert.equal(runCheck(done, {}, { name: 'flowers', code: FLOWERS }).met, true)
})

test('読むだけの API: 探す、持ち物、位置、家、時刻', () => {
  const bot = fakeBot({ '3,64,3': 'poppy' })
  const state = { home: { door: new Vec3(1, 64, 1), inside: new Vec3(2, 64, 2), min: new Vec3(0, 63, 0), max: new Vec3(4, 67, 4) } }
  const code = `function check(w) {
    const f = w.find('poppy', { max_distance: 16 })
    const inv = w.inventory()
    return { met: false, remaining: 1, lines: [JSON.stringify([f, inv.poppy, w.me(), w.home().door, w.time().day, typeof require, typeof fetch])] }
  }`
  const [line] = runCheck(bot, state, { name: 'api', code }).lines
  assert.equal(line, JSON.stringify([[[3, 64, 3]], 2, { x: 0, y: 64, z: 0 }, { x: 1, y: 64, z: 1 }, 1, 'undefined', 'undefined']))
})

test('壊れた判定は理由つきの CheckError（Gemini に返して直させる）', () => {
  const bot = fakeBot()
  const run = (code) => () => runCheck(bot, {}, { name: `c${code.length}`, code })
  assert.throws(run('function check(w) { return { met: 1, remaining: 0 } }'), /met must be true or false/)
  assert.throws(run('function check(w) { return { met: false, remaining: 0 } }'), /remaining is 0 but met is false/)
  assert.throws(run('function check(w) { return { met: false, remaining: -1 } }'), /0 or more/)
  assert.throws(run('function check(w) { return w.nothing() }'), /the check threw/)
  assert.throws(run('function check(w) { while (true) {} }'), /longer than 50 ms/)
  assert.throws(run('function check(w) { for (let i = 0; ; i++) w.block(i, 64, 0) }'), /read more than 20000 blocks/)
  assert.throws(() => validateCheck({ name: 'x', description: 'd', code: 'function check(w) {}' }), /lower_snake_case/)
  assert.throws(() => validateCheck({ name: 'ok_name', description: 'd', code: 'function nope() {}' }), /function check/)
  assert.throws(() => validateCheck({ name: 'ok_name', description: 'd', code: 'async function check(w) {}' }), /synchronous/)
  assert.throws(() => validateCheck({ name: 'ok_name', description: 'd', code: 'function check(w) { (' }), /does not compile/)
})

test('登録: 今もう成り立つ判定は受け取らない（何も確かめていない判定を防ぐ）', () => {
  const state = {}
  const always = 'function check(w) { return { met: true, remaining: 0, lines: ["yes"] } }'
  assert.throws(() => registerCheck(fakeBot(), state, 'always', { description: 'd', code: always }), (e) => e instanceof CheckError && /already met now \(yes\)/.test(e.message))
  const r = registerCheck(fakeBot(), state, 'flowers', { description: '家の前に花を 4 本', code: FLOWERS })
  assert.equal(r.remaining, 4)
  assert.deepEqual(checksList(state), [{ name: 'flowers', description: '家の前に花を 4 本' }])
})

test('custom(name) は条件になる。まだない名前は「まだ」、判定が壊れたら理由を出して未達', () => {
  const knowledge = new Knowledge(md)
  const bot = fakeBot({ '0,64,0': 'poppy' })
  const state = { conditions: {}, home: null, formerHomes: [], builds: {}, memory: {} }
  registerCheck(bot, state, 'flowers', { description: '家の前に花を 4 本', code: FLOWERS })

  const [r] = checkConditions([{ predicate: 'custom', name: 'flowers' }], bot, state, knowledge, bot)
  assert.equal(r.met, false)
  assert.deepEqual(r.lines, ['家の前に花を 4 本: 3 left', 'poppies in front of the door: 1/4'])

  const [unknown] = checkConditions([{ predicate: 'custom', name: 'nothing' }], bot, state, knowledge, bot)
  assert.match(unknown.lines[0], /no check named nothing/)
  assert.throws(() => makeGoal({ predicate: 'custom', name: 'nothing' }, bot, state, knowledge), /no check named nothing/)

  state.conditions.flowers.code = 'function check(w) { throw new Error("oops") }'
  const [broken] = checkConditions([{ predicate: 'custom', name: 'flowers' }], bot, state, knowledge, bot)
  assert.equal(broken.met, false)
  assert.match(broken.lines[0], /the check failed/)
})

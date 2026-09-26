import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { runSkill, validateSkill, SkillStore, SkillError, expectsBaseline, judgeExpects, LIMITS } from '../src/skills.mjs'

const EXPECTS = { predicate: 'have', item: 'food', count: '+1' }
const skill = (body) => ({ code: `export default async function (t, args) {\n${body}\n}`, expects: EXPECTS })

function deps ({ tool = async () => ({ ok: true, result: 'done', seconds: 0 }), judge } = {}) {
  const called = []
  const aborted = []
  return {
    called,
    aborted,
    callTool: async (name, args) => { called.push({ name, args }); return tool(name, args) },
    state: () => ({ mobs: [{ id: 7, name: 'pig', hostile: false }], inventory: {} }),
    blockAt: ({ x }) => ({ name: x === 0 ? 'stone' : 'air' }),
    judge: judge ?? (async (q) => ({ answer: 'yes', confidence: 0.9, question: q.question })),
    abortTool: (reason) => aborted.push(reason)
  }
}

test('a skill calls the tools through the host and ends with done', async () => {
  const d = deps()
  const r = await runSkill(skill(`
    const s = await t.state()
    const r = await t.attack({ entity: s.mobs[0].id })
    await t.log('attacked ' + s.mobs[0].name)
    if (!r.ok) return t.fail(r.result)
    return t.done('hunted ' + s.mobs[0].name + ' with ' + args.weapon)`), { weapon: 'sword' }, d)
  assert.equal(r.ended, 'done')
  assert.equal(r.summary, 'hunted pig with sword')
  assert.deepEqual(d.called, [{ name: 'attack', args: { entity: 7 } }])
  assert.deepEqual(r.log, ['attacked pig'])
  assert.equal(r.calls[0].tool, 'attack')
})

test('a refused tool comes back as a result the skill can read', async () => {
  const d = deps({ tool: async () => ({ ok: false, refused: true, result: 'refused: staying inside for the night', seconds: 0 }) })
  const r = await runSkill(skill('const r = await t.goto({ x: 1, y: 2, z: 3 }); return r.ok ? t.done() : t.fail(r.result)'), {}, d)
  assert.equal(r.ended, 'fail')
  assert.match(r.reason, /staying inside/)
})

test('the sandbox has no Node: no require, process or fetch', async () => {
  const r = await runSkill(skill('return t.done([typeof require, typeof process, typeof fetch, typeof setTimeout].join(","))'), {}, deps())
  assert.equal(r.summary, 'undefined,undefined,undefined,undefined')
})

test('suggestions from the solver are not part of the skill API', async () => {
  const r = await runSkill(skill('return t.done(typeof t.do_suggestion)'), {}, deps())
  assert.equal(r.summary, 'undefined')
})

test('an endless loop is stopped', async () => {
  const r = await runSkill(skill('while (true) {}'), {}, deps(), { ...LIMITS, cpuMs: 100 })
  assert.equal(r.ended, 'stopped')
  assert.match(r.reason, /endless loop|without waiting/)
})

test('an endless loop after awaiting is stopped too', async () => {
  const r = await runSkill(skill('await t.state(); while (true) {}'), {}, deps(), { ...LIMITS, cpuMs: 100 })
  assert.equal(r.ended, 'stopped')
})

test('too many actions stop the skill', async () => {
  const d = deps()
  const r = await runSkill(skill('for (;;) await t.wait()'), {}, d, { ...LIMITS, actions: 3 })
  assert.equal(r.ended, 'stopped')
  assert.match(r.reason, /too many actions \(at most 3\)/)
  assert.equal(d.called.length, 3)
})

test('the time limit stops the skill and the running tool', async () => {
  const d = deps({ tool: () => new Promise(() => {}) })
  const r = await runSkill(skill('await t.goto({ x: 1, y: 2, z: 3 }); return t.done()'), {}, d, { ...LIMITS, seconds: 0.2 })
  assert.equal(r.ended, 'stopped')
  assert.match(r.reason, /time limit/)
  assert.equal(d.aborted.length, 1)
})

test('a stop from outside (the watcher) ends the skill', async () => {
  const control = {}
  const d = deps({ tool: () => new Promise((resolve) => setTimeout(() => resolve({ ok: true, result: 'x', seconds: 0 }), 300)) })
  const running = runSkill(skill('for (;;) await t.wait()'), {}, d, LIMITS, control)
  setTimeout(() => control.stop('watch: a creeper is close'), 50)
  const r = await running
  assert.equal(r.ended, 'stopped')
  assert.equal(r.reason, 'watch: a creeper is close')
})

test('an exception in the skill is an error with its message', async () => {
  const r = await runSkill(skill('null.x'), {}, deps())
  assert.equal(r.ended, 'error')
  assert.match(r.reason, /null/)
})

test('judge asks Jev through the host', async () => {
  const r = await runSkill(skill('const a = await t.judge("Is it dark?"); return t.done(a.answer + " " + a.question)'), {}, deps())
  assert.equal(r.summary, 'yes Is it dark?')
})

test('validate: form, length and syntax', () => {
  assert.throws(() => validateSkill({ description: 'x', expects: EXPECTS, code: 'function () {}' }), /export default async function/)
  assert.throws(() => validateSkill({ description: 'x', expects: EXPECTS, code: 'export default async function (t) { if ( }' }), /does not compile/)
  assert.throws(() => validateSkill({ description: 'x', expects: { predicate: 'explored' }, code: skill('').code }), /expects.predicate/)
  assert.throws(() => validateSkill({ description: 'x', expects: EXPECTS, code: `export default async function (t) {${' '.repeat(4000)}}` }), /longer than 4000/)
  assert.equal(validateSkill({ description: ' 狩り ', expects: EXPECTS, code: skill('').code }).description, '狩り')
})

test('expects: relative counts, parameters and progress', () => {
  let food = 2
  let remaining = 5
  const checked = []
  const d = {
    count: (pred, item) => { assert.equal(pred, 'have'); assert.equal(item, 'food'); return food },
    check: ([spec]) => { checked.push(spec); return [{ met: food >= spec.count, lines: [`food ${food}/${spec.count}`] }] },
    remaining: () => remaining
  }
  const before = expectsBaseline(EXPECTS, {}, d)
  food = 3
  assert.equal(judgeExpects(EXPECTS, {}, before, d).met, true)
  assert.deepEqual(checked[0], { predicate: 'have', item: 'food', count: 3 })

  const param = { predicate: 'have', item: '$item', count: '$n' }
  assert.equal(judgeExpects(param, { item: 'food', n: 4 }, {}, d).met, false)
  assert.throws(() => judgeExpects(param, {}, {}, d), SkillError)

  const progress = { predicate: 'progress' }
  const b = expectsBaseline(progress, {}, d)
  remaining = 4
  assert.equal(judgeExpects(progress, {}, b, d).met, true)
})

test('store: versions, counts, the list and the cleaning outside the stream', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-'))
  const store = new SkillStore(dir)
  const def = { description: '狩り', expects: EXPECTS, code: skill('return t.done()').code }
  assert.throws(() => store.put('Bad Name', def), /snake_case/)
  assert.equal(store.put('hunt', def), 1)
  assert.equal(store.put('hunt', { ...def, description: '狩り v2' }), 2)
  assert.equal(store.get('hunt').version, 2)
  assert.equal(store.get('hunt', 1).description, '狩り')
  assert.equal(store.record('hunt', 1, true), true) // 初めて成功
  assert.equal(store.record('hunt', 1, true), false)
  store.record('hunt', 2, false, 'no pig')
  store.put('broken', def)
  store.record('broken', 1, false, 'error')
  const [broken, hunt] = store.list()
  assert.equal(broken.verified, false)
  assert.deepEqual([hunt.uses, hunt.successes, hunt.failures, hunt.last_failure], [3, 2, 1, 'no pig'])
  assert.deepEqual([hunt.verified, hunt.last_good_version], [false, 1])

  assert.deepEqual(store.clean({ dryRun: true }), { removed: ['broken'], pruned: [{ name: 'hunt', versions: [2] }] })
  assert.equal(store.names().length, 2)
  store.clean()
  assert.deepEqual(store.names(), ['hunt'])
  assert.deepEqual(store.read('hunt').versions.map((v) => v.version), [1])
})

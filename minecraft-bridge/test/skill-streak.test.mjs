// 同じ理由で失敗し続ける技は動かさない（設計書 120）: 畑の技が 143 回のうち 140 回、同じ理由で失敗した
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { SkillStore, blockedReason, failingStreak, SKILL_STREAK_LIMIT, SKILL_PROBE_MS } from '../src/skills.mjs'

const EXPECTS = { predicate: 'progress' }
const def = { description: '畑', expects: EXPECTS, code: 'export default async function (t) { return t.done() }' }
const fresh = () => {
  const store = new SkillStore(fs.mkdtempSync(path.join(os.tmpdir(), 'skills-')))
  store.put('farm', def)
  return store
}
const fail = (store, n, reason = "expects not met: the small goal's remaining work 41 -> 41") => {
  for (let i = 0; i < n; i++) store.record('farm', 1, false, reason)
}

test('同じ理由の失敗が続く数を、技ごとに数える（数字の違いは同じ理由。成功で 0、違う理由で 1 に戻る）', () => {
  const store = fresh()
  fail(store, 3, 'remaining work 41 -> 41')
  assert.equal(store.get('farm').streak, 3)
  store.record('farm', 1, false, 'remaining work 40 -> 40') // 数字が違うだけ
  assert.equal(store.get('farm').streak, 4)
  store.record('farm', 1, false, 'no wheat_seeds') // 違う理由
  assert.equal(store.get('farm').streak, 1)
  fail(store, 2, 'no wheat_seeds')
  store.record('farm', 1, true)
  assert.equal(store.get('farm').streak, 0)
})

test('一覧: 失敗が続くと verified でなくなり、failing と streak を示す。成功の記録は残る', () => {
  const store = fresh()
  store.record('farm', 1, true)
  assert.equal(store.list()[0].verified, true)
  fail(store, SKILL_STREAK_LIMIT - 1)
  const before = store.list()[0]
  assert.deepEqual([before.verified, before.failing, before.streak], [true, false, SKILL_STREAK_LIMIT - 1])
  fail(store, 1)
  const after = store.list()[0]
  assert.deepEqual([after.verified, after.failing, after.streak, after.successes], [false, true, SKILL_STREAK_LIMIT, 1])
  // 新しい版（書き直し）は数え直す
  assert.equal(store.put('farm', def), 2)
  assert.deepEqual([store.list()[0].failing, store.list()[0].streak], [false, 0])
})

test('動かさない理由: 続いた失敗が上限に達し、前の失敗から試し直しの時間がたっていないときだけ。たてば 1 回試せる', () => {
  const store = fresh()
  fail(store, SKILL_STREAK_LIMIT - 1)
  assert.equal(blockedReason(store.get('farm'), null), null)
  fail(store, 1)
  const skill = store.get('farm')
  const now = new Date(skill.last_used).getTime()
  const why = blockedReason(skill, null, now + 1000)
  assert.match(why, /^farm v1 failed 5 times in a row for the same reason \(expects not met: the small goal's remaining work 41 -> 41\)/)
  assert.match(why, /write a revised version/)
  assert.doesNotMatch(why, /which worked/)
  assert.match(why, /tried again 10 min from now/)
  // 前に成功した版があれば、それを動かせと言う
  assert.match(blockedReason({ ...skill, version: 2 }, 1, now + 1000), /run v1, which worked/)
  // 時間がたてば試せる。試して失敗すれば、また止まる
  assert.equal(blockedReason(skill, null, now + SKILL_PROBE_MS), null)
  fail(store, 1)
  assert.equal(failingStreak(store.get('farm')), SKILL_STREAK_LIMIT + 1)
})

// 任意の条件（設計書 42）: custom(name)。
//
// 組み込みの述語で書けない頼み（羊を柵に 3 匹、家の前に花を 4 本…）のために、Gemini が判定の
// コード（JS）を書く。完了はそのコードが世界を読んで決める（モデルの言うことからは決めない、の
// 原則はそのまま）。コードは isolated-vm の中で、読むだけの API（ブロック、探す、持ち物、位置、家、
// チェストの記憶、モブ、時刻）で同期に動く（/check と /observe の評価は同期なので）。書いた時点で
// もう成り立っていれば受け取らない（何も確かめていない判定を防ぐ）。
//
//   function check(w) { ...; return { met: boolean, remaining: number, lines: [string] } }
//
// 判定は世界のもの（state.json に保存。技と違い、ワールドを作り直せば消える）。

import ivm from 'isolated-vm'
import vec3Pkg from 'vec3'
import { inventoryCounts, isHostile, nearbyEntities } from './observe.mjs'
import { storedCounts } from './memory.mjs'

const { Vec3 } = vec3Pkg

export const CHECK_LIMITS = { cpuMs: 50, reads: 20000, finds: 20, memoryMb: 16 }
export const MAX_CHECK_CHARS = 3000
const MAX_LINES = 5
const MAX_LINE = 160
const NAME = /^[a-z][a-z0-9_]{1,39}$/
const FIND_MAX_DISTANCE = 64
const FIND_MAX_COUNT = 256

export class CheckError extends Error {}
const bad = (why) => { throw new CheckError(why) }

const BOOT = `
const __j = (s) => (s === undefined ? undefined : JSON.parse(s))
const w = Object.freeze({
  block: (x, y, z) => __j(__block(Number(x), Number(y), Number(z))),
  find: (name, opts) => __j(__find(String(name), JSON.stringify(opts ?? {}))),
  inventory: () => __j(__inventory()),
  me: () => __j(__me()),
  home: () => __j(__home()),
  chests: () => __j(__chests()),
  mobs: (name, radius) => __j(__mobs(String(name ?? ''), Number(radius ?? 32))),
  time: () => __j(__time())
})
`

// 形を確かめて、コンパイルできる（check という関数がある）ことを確かめる
export function validateCheck ({ name, description, code }) {
  if (!NAME.test(String(name ?? ''))) bad('the name must be lower_snake_case (2-40 characters)')
  if (typeof description !== 'string' || !description.trim()) bad('description is required')
  if (typeof code !== 'string' || !code.trim()) bad('code is required')
  if (code.length > MAX_CHECK_CHARS) bad(`the code is longer than ${MAX_CHECK_CHARS} characters`)
  if (!/function\s+check\s*\(/.test(code)) bad('the code must define `function check(w) { ... }`')
  if (/\bawait\b|\basync\b|\bPromise\b/.test(code)) bad('the check must be synchronous (no async / await / Promise)')
  const isolate = new ivm.Isolate({ memoryLimit: 8 })
  try {
    isolate.compileScriptSync(code)
  } catch (e) {
    bad(`the code does not compile: ${e.message}`)
  } finally {
    isolate.dispose()
  }
  return { description: description.trim(), code }
}

// 世界を読む API（ホスト側）。reads と finds を数える
function hostApi (bot, state, counter, limits) {
  const read = () => { if (++counter.reads > limits.reads) bad(`the check read more than ${limits.reads} blocks`) }
  const plain = (v) => v && { x: v.x, y: v.y, z: v.z }
  return {
    block (x, y, z) {
      read()
      if (![x, y, z].every(Number.isFinite)) bad('block(x, y, z) needs numbers')
      const b = bot.blockAt(new Vec3(Math.floor(x), Math.floor(y), Math.floor(z)))
      if (!b) return 'null' // 見えていない（読み込まれていない）
      const props = b.getProperties?.() ?? {}
      return JSON.stringify({ name: b.name, solid: b.boundingBox === 'block', ...(Object.keys(props).length ? { props } : {}) })
    },
    find (name, opts) {
      if (++counter.finds > limits.finds) bad(`the check searched more than ${limits.finds} times`)
      const o = JSON.parse(opts)
      const ids = Object.values(bot.registry.blocksByName).filter((b) => b.name === name || (name.startsWith('*') && b.name.endsWith(name.slice(1)))).map((b) => b.id)
      if (!ids.length) bad(`there is no block named ${name} (use *_suffix for kinds, e.g. *_flower is not a block name)`)
      const near = Array.isArray(o.near) ? new Vec3(...o.near.map(Number)) : bot.entity.position
      const found = bot.findBlocks({
        point: near,
        matching: ids,
        maxDistance: Math.min(Number(o.max_distance ?? 32), FIND_MAX_DISTANCE),
        count: Math.min(Number(o.count ?? 64), FIND_MAX_COUNT)
      })
      return JSON.stringify(found.map((p) => [p.x, p.y, p.z]))
    },
    inventory: () => JSON.stringify(inventoryCounts(bot)),
    me: () => JSON.stringify(plain(bot.entity.position.floored())),
    home: () => JSON.stringify(state.home ? { door: plain(state.home.door), inside: plain(state.home.inside), min: plain(state.home.min), max: plain(state.home.max) } : null),
    chests: () => JSON.stringify(storedCounts(state.memory ?? {}, state.home)),
    mobs (name, radius) {
      const r = Math.min(radius || 32, 64)
      return JSON.stringify(nearbyEntities(bot).filter(({ e, dist }) => dist <= r && e.type !== 'player' && e.name !== 'item' && (!name || e.name === name))
        .map(({ e }) => ({ name: e.name, x: Math.floor(e.position.x), y: Math.floor(e.position.y), z: Math.floor(e.position.z), hostile: isHostile(bot, e) })))
    },
    time: () => JSON.stringify({ time_of_day: bot.time.timeOfDay, day: Math.floor((bot.time.age ?? 0) / 24000) })
  }
}

// コンパイルした判定を覚えておく（/observe は毎ステップ評価する）
const compiled = new Map()

function prepared (name, code, limits) {
  const key = `${name}\n${code}`
  let entry = compiled.get(key)
  if (entry) return entry
  for (const [k, e] of compiled) if (k.startsWith(`${name}\n`)) { e.isolate.dispose(); compiled.delete(k) }
  const isolate = new ivm.Isolate({ memoryLimit: limits.memoryMb })
  const context = isolate.createContextSync()
  const holder = { api: null }
  const g = context.global
  for (const fn of ['block', 'find', 'inventory', 'me', 'home', 'chests', 'mobs', 'time']) {
    g.setSync(`__${fn}`, new ivm.Callback((...args) => holder.api[fn](...args)))
  }
  context.evalSync(BOOT)
  context.evalSync(code, { timeout: limits.cpuMs })
  entry = { isolate, context, holder }
  compiled.set(key, entry)
  return entry
}

// 判定を 1 回動かす → { met, remaining, lines }。だめなら CheckError（理由は Gemini に返す）
export function runCheck (bot, state, { name, code }, limits = CHECK_LIMITS) {
  let entry
  try {
    entry = prepared(name, code, limits)
  } catch (e) {
    if (e instanceof CheckError) throw e
    bad(`the code failed to load: ${e.message}`)
  }
  const counter = { reads: 0, finds: 0 }
  entry.holder.api = hostApi(bot, state, counter, limits)
  let out
  try {
    out = JSON.parse(entry.context.evalSync('JSON.stringify(check(w))', { timeout: limits.cpuMs }) ?? 'null')
  } catch (e) {
    if (/timed out/.test(e.message)) bad(`the check ran longer than ${limits.cpuMs} ms (read fewer blocks)`)
    bad(`the check threw: ${e.message}`)
  } finally {
    entry.holder.api = null
  }
  if (!out || typeof out !== 'object') bad('check(w) must return { met, remaining, lines }')
  if (typeof out.met !== 'boolean') bad('met must be true or false')
  const remaining = out.met ? 0 : Number(out.remaining)
  if (!Number.isFinite(remaining) || remaining < 0) bad('remaining must be a number of 0 or more (what is left to do: it has to go down as the work goes on)')
  if (!out.met && remaining === 0) bad('remaining is 0 but met is false: remaining must count what is left')
  const lines = (Array.isArray(out.lines) ? out.lines : []).slice(0, MAX_LINES).map((l) => String(l).slice(0, MAX_LINE))
  return { met: out.met, remaining: Math.ceil(remaining), lines }
}

// PUT /conditions/<name>: 確かめて、今はまだ成り立っていないことを確かめてから登録する
export function registerCheck (bot, state, name, body) {
  const def = validateCheck({ name, ...body })
  const r = runCheck(bot, state, { name, code: def.code })
  if (r.met) bad(`the check is already met now (${r.lines.join('; ') || 'no lines'}): it must test what is still to be done`)
  state.conditions ??= {}
  state.conditions[name] = { description: def.description, code: def.code, created: Date.now() }
  return r
}

export function checksList (state) {
  return Object.entries(state.conditions ?? {}).map(([name, c]) => ({ name, description: c.description }))
}

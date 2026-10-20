// 技（設計書 22）: Gemini が書いた JS の手順を、道具の API だけを見せたサンドボックスで実行する。
//
// サンドボックスは isolated-vm（V8 の別の isolate）。技から見えるのは t.<道具>() などの API だけで、
// ファイル・ネットワーク・process は最初から存在しない。API の呼び出しはブリッジ本体の callTool
// （A の道具と同じ検証と安全の制約）を通る。上限（時間、行動の数、CPU、メモリ）を超えるか、外から
// 止められたら、isolate を破棄して終わらせる。成功かどうかは、技が宣言した expects を世界で判定する
// （技の done() だけでは成功にしない）。
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import ivm from 'isolated-vm'
import { Vec3 } from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
import { ACTION_TOOLS, QUERY_TOOLS } from './tools.mjs'

export const LIMITS = {
  actions: 40, // 行動の道具の呼び出し
  queries: 200, // 調べもの・state()・block_at()
  judges: 10, // Jev への質問
  cpuMs: 1000, // isolate の中で使った CPU 時間（await せずに回るループを止める）
  memoryMb: 32,
  judgeWaitMs: 5000, // judge の答えを待つ時間
  rawMs: 10 * 60 * 1000 // 生の bot を触る技の上限（await で止まらない暴走の見張りは効かないので、時間で切る）
}
// 生の mineflayer の bot を技に見せるモード（設計書 168）。BRIDGE_RAW_BOT=1 のときだけ、技を raw: true で保存でき、
// その技はメインプロセスの vm で動く（isolate ではない: ファイル・ネットワークに届く。使い捨ての世界でだけ使う）
// Python の設定（minecraft.agent.skill_raw_bot）と同じ解釈: 空・0・false・no・off 以外は有効
export const rawBotEnabled = () => !['', '0', 'false', 'no', 'off'].includes((process.env.BRIDGE_RAW_BOT ?? '').trim().toLowerCase())
// 判断なしの実験（設計書 191）。BRIDGE_RAW_ONLY=1 のとき、技は生の bot（raw: true）だけで書く: t.goto などの道具（ブリッジの
// 断りと state.goal に依存する）、ソルバーや記録の調べもの、判定の質問、助言入りの state は技から消える。
// 成功の条件（expects）は、目標に頼らない 4 つだけ
export const rawOnlyEnabled = () => !['', '0', 'false', 'no', 'off'].includes((process.env.BRIDGE_RAW_ONLY ?? '').trim().toLowerCase())
// 調べもの: ゲームの知識（レシピ）と、見えるだけの断面。ブリッジが世界から導く find_blocks（tree / quarry / exposed）、inspect（断る理由）などは外す
export const RAW_ONLY_QUERIES = ['recipe_of', 'find_recipes', 'uses_of', 'cross_section']
const RAW_ONLY_PREDICATES = ['have', 'at', 'height', 'out_of_closed_space', 'block_near']
export const BLOCK_NEAR_MAX = 32 // block_near で探す半径の上限
export const MAX_CODE_CHARS = 4000
export const MAX_DESCRIPTION_CHARS = 200
const MAX_LOG = 50
const MAX_CALLS_SHOWN = 30
const EXPECT_PREDICATES = ['have', 'stored', 'built', 'placed', 'lit', 'planted', 'farmed', 'leveled', 'custom', 'facility', 'progress', 'at', 'height', 'out_of_closed_space', 'block_near']
const NAME = /^[a-z][a-z0-9_]{1,39}$/
// 技から呼べる行動（ソルバーの提案は技からは使わない: 技はソルバーに依存しない手順にする）
export const SKILL_ACTIONS = ACTION_TOOLS.filter((n) => n !== 'do_suggestion')
export const DEFAULT_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data', 'skills')

const ENDLESS = 'the code kept running without waiting (an endless loop?)'

export class SkillError extends Error {}
const bad = (why) => { throw new SkillError(why) }

// 技のコードを、isolate で実行できるスクリプトにする（export default を変数に置き換える）
function wrap (code) {
  if (!/export\s+default\s+async\s+function\b/.test(code)) bad('the code must be `export default async function (t, args) { ... }`')
  return code.replace(/export\s+default\s+/, 'const __skill = ')
}

// 形と構文を確かめて、保存する形にする。だめなら SkillError（理由は Gemini に返す）
export function validateSkill (def) {
  const { description, params, expects, code, raw } = def ?? {}
  if (typeof description !== 'string' || !description.trim()) bad('description is required')
  if (description.length > MAX_DESCRIPTION_CHARS) bad(`description is longer than ${MAX_DESCRIPTION_CHARS} characters`)
  if (params != null && (typeof params !== 'object' || Array.isArray(params))) bad('params must be a JSON Schema object')
  if (typeof code !== 'string' || !code.trim()) bad('code is required')
  if (code.length > MAX_CODE_CHARS) bad(`the code is longer than ${MAX_CODE_CHARS} characters`)
  validateExpects(expects)
  if (rawOnlyEnabled()) {
    if (!raw) bad('only raw skills are allowed here: set raw to true and write the code over t.bot (the t.goto / t.dig tools are not available)')
    if (!RAW_ONLY_PREDICATES.includes(expects.predicate)) bad(`expects.predicate must be one of ${RAW_ONLY_PREDICATES.join(', ')} here (the others need a goal or a plan)`)
  }
  if (raw != null && typeof raw !== 'boolean') bad('raw must be true or false')
  if (raw && !rawBotEnabled()) bad('raw skills are off here (the bridge was not started with BRIDGE_RAW_BOT=1): write the skill over the t.* tools only')
  const isolate = new ivm.Isolate({ memoryLimit: 8 })
  try {
    isolate.compileScriptSync(wrap(code))
  } catch (e) {
    bad(`the code does not compile: ${e.message}`)
  } finally {
    isolate.dispose()
  }
  return { description: description.trim(), params: params ?? { type: 'object', properties: {} }, expects, code, ...(raw ? { raw: true } : {}) }
}

function validateExpects (e) {
  if (!e || typeof e !== 'object') bad(`expects is required: { predicate: ${EXPECT_PREDICATES.join(' | ')}, ... }`)
  if (!EXPECT_PREDICATES.includes(e.predicate)) bad(`expects.predicate must be one of ${EXPECT_PREDICATES.join(', ')}`)
  if (['have', 'stored'].includes(e.predicate)) {
    if (e.item == null) bad(`expects ${e.predicate} needs an item`)
    if (e.count == null) bad(`expects ${e.predicate} needs a count (a number, "+N" or "$param")`)
    // 0 個は「ない」ことで、持ち物の述語では判定できない（置いた物は持ち物から消える: 置いたかは block_near で見る）
    if (typeof e.count === 'number' && !(e.count >= 1)) bad(`expects ${e.predicate}: count must be 1 or more (got ${e.count}); to check that something was placed, use block_near { block, distance }`)
  }
  if (e.predicate === 'block_near') {
    if (typeof e.block !== 'string' || !(e.block.startsWith('$') || /^[a-z][a-z0-9_]*$/.test(e.block))) bad('expects block_near needs block: a block name such as "crafting_table" (or "$param")')
    if (e.distance != null && !(typeof e.distance === 'string' && e.distance.startsWith('$')) && !(Number(e.distance) >= 1 && Number(e.distance) <= BLOCK_NEAR_MAX)) bad(`expects block_near: distance must be 1 to ${BLOCK_NEAR_MAX} blocks`)
  }
  if (e.predicate === 'custom' && !e.name) bad('expects custom needs the name of a check (a custom objective condition)')
  if (e.predicate === 'facility' && !e.name) bad('expects facility needs the name of a designed facility (search_facility)')
  if (e.predicate === 'at' && (typeof e.where !== 'string' || !(e.where.startsWith('$') || parseSpot(e.where)))) {
    bad('expects at needs where: "x,y,z" (or "$param") — you stand within distance (default 2) blocks of it')
  }
  if (e.predicate === 'at' && e.min_change != null && !(Number(e.min_change) > 0) && !(typeof e.min_change === 'string' && e.min_change.startsWith('$'))) {
    bad('expects at: min_change must be a number above 0 (how many blocks closer than the start also counts as success)')
  }
  if (e.predicate === 'height') {
    if (e.y == null || (typeof e.y === 'string' && !e.y.startsWith('$') && !Number.isFinite(Number(e.y)))) bad('expects height needs y: a number (or "$param")')
    if (!['down', 'up'].includes(e.direction)) bad('expects height needs direction: "down" or "up"')
  }
}

// "x,y,z" → { x, y, z } か null
function parseSpot (text) {
  const m = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(String(text))
  return m ? { x: Number(m[1]), y: Number(m[2]), z: Number(m[3]) } : null
}

// "$param" を引数の値に置き換える
function substitute (value, args) {
  if (typeof value === 'string' && value.startsWith('$')) {
    const v = args?.[value.slice(1)]
    if (v === undefined) bad(`expects refers to ${value} but the argument is missing`)
    return v
  }
  return value
}

// 実行の前: 相対の数（"+N"）のための今の数と、progress のための今の残り
export function expectsBaseline (expects, args, deps) {
  if (expects.predicate === 'progress') return { remaining: deps.remaining() }
  // 走らせる前に、成功を判定できる引数か確かめる（走らせたあとで「判定できない」と分かると、数分の作業が無駄な失敗になる:
  // 2026-10-17 descend_stair_raw の dest に "mine_4" や "new" を渡して、毎回走ってから失敗した）
  if (expects.predicate === 'height') {
    const goal = substitute(expects.y, args)
    if (!Number.isFinite(Number(goal))) bad(`expects height cannot be judged: y must be a number (got ${goal}); nothing was run`)
    return { y: deps.where().y }
  }
  if (expects.predicate === 'at') {
    const where = substitute(expects.where, args)
    if (!parseSpot(where)) bad(`expects at cannot be judged: where must be "x,y,z" with numbers (got ${where}); nothing was run. Pass the coordinates, not a name`)
    const here = deps.where()
    return { x: here.x, y: here.y, z: here.z }
  }
  if (expects.predicate === 'block_near') {
    const block = substitute(expects.block, args)
    if (!deps.knownBlock(block)) bad(`expects block_near cannot be judged: "${block}" is not a block name; nothing was run`)
    return {}
  }
  if (!['have', 'stored', 'planted', 'farmed'].includes(expects.predicate)) return {}
  const count = substitute(expects.count, args)
  if (typeof count === 'string' && count.startsWith('+')) {
    return { count: deps.count(expects.predicate, String(substitute(expects.item, args))) }
  }
  return {}
}

// 実行の後: expects が成り立つか → { met, lines }
export function judgeExpects (expects, args, before, deps) {
  if (expects.predicate === 'progress') {
    const now = deps.remaining()
    if (before.remaining == null || now == null) return { met: false, lines: ['there is no small goal to measure progress on'] }
    return { met: now < before.remaining, lines: [`the small goal's remaining work ${before.remaining} -> ${now}`] }
  }
  // そばにあるブロック: 置いた物は持ち物から消えるので、置けたかは世界のブロックで見る（世界の事実だけ。設計書 193）
  if (expects.predicate === 'block_near') {
    const block = substitute(expects.block, args)
    const distance = Math.min(BLOCK_NEAR_MAX, Math.max(1, Number(substitute(expects.distance, args) ?? 5)))
    const found = deps.blockNear(block, distance)
    return { met: Boolean(found), lines: [found ? `${block} is ${found.distance} blocks away at ${found.x},${found.y},${found.z}` : `no ${block} within ${distance} blocks`] }
  }
  // 居場所: 技が世界のどこに立って終わったか。持ち物や施設の述語では言えない移動・脱出の成功をここで判定する
  if (expects.predicate === 'out_of_closed_space') {
    // 積み上がって、まわりが崖の柱の頂上・屋根に出ただけなら出られていない（閉じた所ではないが、降りる手がない）
    const here = deps.where()
    const why = here.shut ? 'still in a closed space (no walking out of here)' : here.isolated ? 'you stand on a pillar top or roof with a drop on every side: not out' : null
    return { met: !why, lines: [why ?? 'you can walk out of where you stand'] }
  }
  if (expects.predicate === 'at') {
    const goal = parseSpot(substitute(expects.where, args))
    if (!goal) return { met: false, lines: [`expects at cannot be judged: where is not "x,y,z" (${substitute(expects.where, args)})`] }
    const here = deps.where()
    const radius = Number(substitute(expects.distance, args) ?? 2)
    const flat = Math.hypot(here.x - (goal.x + 0.5), here.z - (goal.z + 0.5))
    const high = Math.abs(here.y - goal.y)
    const lines = [`you stand ${flat.toFixed(1)} blocks (${high.toFixed(1)} in height) from ${goal.x},${goal.y},${goal.z}; within ${radius} blocks and 2 in height is the goal`]
    const reached = flat <= radius && high <= 2
    // 近づいた分も成功: 目標への距離（水平と高さの合成）が始めより min_change 以上減った（途中まで歩いて、もう一度呼んで続ける）
    if (expects.min_change != null && before.x != null) {
      const need = Number(substitute(expects.min_change, args))
      const away = (p) => Math.hypot(p.x - (goal.x + 0.5), p.y - goal.y, p.z - (goal.z + 0.5))
      const closer = away(before) - away(here)
      lines.push(`${closer.toFixed(1)} blocks closer than at the start; counts as progress from ${need}`)
      return { met: reached || closer >= need, lines }
    }
    return { met: reached, lines }
  }
  // 高さ: 目標の高さに着いたか、そこへ向かって min_change 以上進んだか（途中まででも進んだ分は成功: もう一度呼んで続ける）
  if (expects.predicate === 'height') {
    const goal = Number(substitute(expects.y, args))
    if (!Number.isFinite(goal)) return { met: false, lines: [`expects height cannot be judged: y is not a number (${substitute(expects.y, args)})`] }
    const sign = expects.direction === 'down' ? -1 : 1
    const now = deps.where().y
    const moved = (now - (before.y ?? now)) * sign
    const need = Number(substitute(expects.min_change, args) ?? 3)
    const reached = (goal - now) * sign <= 0
    const way = expects.direction === 'down' ? 'lower' : 'higher'
    return { met: reached || moved >= need, lines: [`y ${(before.y ?? now).toFixed(1)} -> ${now.toFixed(1)} (${moved.toFixed(1)} ${way}); ${reached ? `reached y ${goal}` : `goal y ${goal}; counts as progress from ${need} ${way}`}`] }
  }
  const spec = {}
  for (const [k, v] of Object.entries(expects)) spec[k] = substitute(v, args)
  if (typeof spec.count === 'string' && spec.count.startsWith('+')) spec.count = (before.count ?? 0) + Number(spec.count.slice(1))
  if (spec.count != null) spec.count = Number(spec.count)
  try {
    const [r] = deps.check([spec])
    return { met: r.met, lines: r.lines }
  } catch (e) {
    return { met: false, lines: [`expects cannot be judged: ${e.message}`] }
  }
}

// isolate の中の API（t）。呼び出しはすべて __call（ホストの非同期関数）を通る
const BOOT = `
const __host = globalThis.__call
delete globalThis.__call
const __c = (kind, name, args) => __host.apply(undefined, [kind, name, JSON.stringify(args ?? {})],
  { arguments: { copy: true }, result: { promise: true, copy: true } }).then(JSON.parse)
const t = {}
for (const n of ${JSON.stringify(SKILL_ACTIONS)}) t[n] = (args) => __c('action', n, args)
for (const n of ${JSON.stringify(QUERY_TOOLS)}) t[n] = (args) => __c('query', n, args)
t.state = () => __c('query', 'state')
t.block_at = (args) => __c('query', 'block_at', args)
t.judge = (question, options) => __c('judge', 'judge', { question: String(question), ...(options ?? {}) })
t.log = (message) => __c('log', 'log', { message: String(message) })
t.done = (summary = '') => ({ __end: 'done', summary: String(summary) })
t.fail = (reason = '') => ({ __end: 'fail', reason: String(reason) })
Object.freeze(t)
`

// 止められた技の bot に触れたときの例外。index.mjs が、技の外から投げられても落とさないために目印を付ける
export const SKILL_STOPPED = 'skillStopped'
function stoppedError (reason) {
  const e = new Error(reason)
  e[SKILL_STOPPED] = true
  return e
}

// 生の bot を渡す技のために、技が付けたリスナーを覚えておいて終わりに外す（残すと、次の技や反射と二重に動く）。
// 関数は元のオブジェクトに束ねて返す（Proxy のまま呼ぶと内部の this がずれる）
function tracked (target, owned) {
  const ADD = new Set(['on', 'once', 'addListener', 'prependListener'])
  return new Proxy(target, {
    get (obj, prop) {
      // 止められた後に、進行中の待ち（dig など）から戻った技が、反射の握った操作キーや経路を書き換えない
      if (owned.stopped?.()) throw stoppedError(owned.stopped())
      const value = Reflect.get(obj, prop)
      if (typeof prop === 'string' && ADD.has(prop) && typeof value === 'function') {
        return (event, fn) => {
          owned.listeners.push([obj, event, fn])
          return value.call(obj, event, fn)
        }
      }
      if (prop === '_client' && value && typeof value === 'object') return tracked(value, owned)
      return typeof value === 'function' ? (...a) => value.apply(obj, a) : value
    }
  })
}

// 生の bot を触った技が残すもの（操作キー、経路、掘り、リスナー、タイマー）を止める
function releaseBot (bot, owned) {
  for (const [obj, event, fn] of owned.listeners) { try { obj.removeListener(event, fn) } catch {} }
  for (const id of owned.timers) { clearTimeout(id); clearInterval(id) }
  owned.listeners.length = 0
  owned.timers.clear()
  try { bot.clearControlStates?.() } catch {}
  try { bot.pathfinder?.setGoal?.(null) } catch {}
  try { bot.stopDigging?.() } catch {}
  // 技が setMovements(new Movements(t.bot)) で渡した経路の設定は、t.bot を抱えたまま pathfinder に残る。止められたあとは
  // t.bot が例外を投げるので、次に反射や別の技が歩くと物理の tick の中で投げて、ブリッジごと落ちる
  // （2026-10-18 21:17 ベンチ: 反射の逃走で落ち、そのあと 24 分間ブリッジがなかった）。技を始める前の設定に戻す
  try {
    const pf = bot.pathfinder
    if (pf && pf.movements !== owned.movements) pf.setMovements(owned.movements ?? new pathfinderPkg.Movements(bot))
  } catch {}
}

const clip = (s, n = 160) => { const text = typeof s === 'string' ? s : JSON.stringify(s); return text.length > n ? `${text.slice(0, n)}…` : text }

// 技を 1 回実行する。deps: { callTool(name, args), state(), blockAt(args), judge(q, signal), abortTool(reason) }
// 戻り値: { ended: 'done' | 'fail' | 'error' | 'stopped', summary?, reason?, calls, log, seconds }
// control（任意）: 実行中に stop(reason) と、表示用の { calls, current } を入れる
export async function runSkill (skill, args, deps, limits = LIMITS, control = {}) {
  const started = Date.now()
  const calls = []
  const log = []
  const used = { action: 0, query: 0, judge: 0 }
  let stopped = null
  let current = null
  const raw = !!skill.raw
  const isolate = raw ? null : new ivm.Isolate({ memoryLimit: limits.memoryMb })
  const owned = { listeners: [], timers: new Set(), stopped: () => stopped }
  let rawStop = null
  const judgeAbort = new AbortController()

  const stop = (reason) => {
    if (stopped) return
    stopped = reason
    judgeAbort.abort()
    if (current) deps.abortTool?.(reason)
    try { isolate?.dispose() } catch {}
    if (raw) {
      releaseBot(deps.bot, owned)
      rawStop?.()
    }
  }
  control.stop = stop
  control.raw = raw
  // 実況のために、動いている間の経過を見せる: 経った秒数と、技が書いた最後の数行（t.log）
  control.view = () => ({ calls: used.action + used.query, current, seconds: Math.round((Date.now() - started) / 1000), log: log.slice(-3) })

  const over = (kind) => {
    const max = { action: limits.actions, query: limits.queries, judge: limits.judges }[kind]
    if (++used[kind] > max) {
      stop(`too many ${kind === 'query' ? 'lookups' : `${kind}s`} (at most ${max})`)
      throw new Error(stopped)
    }
  }

  async function dispatch (kind, name, argsJson) {
    if (stopped) throw new Error(stopped)
    const a = JSON.parse(argsJson)
    if (kind === 'log') {
      if (log.length < MAX_LOG) log.push(clip(a.message, 200))
      return null
    }
    if (kind === 'judge') {
      over('judge')
      return await deps.judge(a, judgeAbort.signal, limits.judgeWaitMs)
    }
    if (kind === 'query') {
      over('query')
      if (name === 'state') return rawOnlyEnabled() ? deps.rawState() : deps.state()
      if (name === 'block_at') return deps.blockAt(a)
      const r = await deps.callTool(name, a)
      calls.push({ tool: name, args: a, ok: r.ok })
      return r
    }
    over('action')
    current = `${name}(${clip(a, 80)})`
    try {
      const r = await deps.callTool(name, a)
      calls.push({ tool: name, args: a, ok: r.ok, result: clip(r.result), seconds: r.seconds })
      return r
    } finally {
      current = null
    }
  }

  const cpu = isolate && setInterval(() => {
    try {
      if (Number(isolate.cpuTime) / 1e6 > limits.cpuMs) stop(ENDLESS)
    } catch {}
  }, 100)
  try {
    if (raw) return finish(await runRaw())
    const context = await isolate.createContext()
    // 止めた後に終わった道具の結果は、破棄した isolate に返さない
    await context.global.set('__call', new ivm.Reference((kind, name, argsJson) =>
      dispatch(kind, name, argsJson).then((r) => stopped ? 'null' : JSON.stringify(r ?? null))))
    await context.global.set('__args', JSON.stringify(args ?? {}))
    const script = await isolate.compileScript(`${BOOT}\n${wrap(skill.code)};\n` +
      ';(async () => { const r = await __skill(t, JSON.parse(__args)); return JSON.stringify(r ?? null) })()')
    const out = JSON.parse(await script.run(context, { timeout: limits.cpuMs, promise: true, copy: true }))
    if (stopped) return finish({ ended: 'stopped', reason: stopped })
    if (out?.__end === 'fail') return finish({ ended: 'fail', reason: out.reason || 'the skill gave up' })
    return finish({ ended: 'done', summary: out?.__end === 'done' ? out.summary : clip(out ?? '') })
  } catch (e) {
    if (stopped) return finish({ ended: 'stopped', reason: stopped })
    // 最初の同期の実行が時間切れ（CPU の見張りと同じこと）
    if (/execution timed out/.test(e.message)) return finish({ ended: 'stopped', reason: ENDLESS })
    return finish({ ended: 'error', reason: clip(e.message, 300) })
  } finally {
    clearInterval(cpu)
    try { isolate?.dispose() } catch {}
    if (raw) releaseBot(deps.bot, owned)
  }

  // 生の bot の技: メインプロセスの vm。t は isolate の中と同じ API を、ホストの関数として渡す
  async function runRaw () {
    if (!deps.bot) return { ended: 'error', reason: 'raw skills need the bot (deps.bot)' }
    const call = (kind, name) => async (a) => JSON.parse(JSON.stringify((await dispatch(kind, name, JSON.stringify(a ?? {}))) ?? null))
    const t = {}
    const only = rawOnlyEnabled()
    if (!only) for (const n of SKILL_ACTIONS) t[n] = call('action', n)
    for (const n of only ? RAW_ONLY_QUERIES : QUERY_TOOLS) t[n] = call('query', n)
    t.state = call('query', 'state')
    t.block_at = call('query', 'block_at')
    if (!only) t.judge = (question, options) => call('judge', 'judge')({ question: String(question), ...(options ?? {}) })
    t.log = (message) => call('log', 'log')({ message: String(message) })
    t.done = (summary = '') => ({ __end: 'done', summary: String(summary) })
    t.fail = (reason = '') => ({ __end: 'fail', reason: String(reason) })
    owned.movements = deps.bot.pathfinder?.movements
    t.bot = tracked(deps.bot, owned)
    t.Vec3 = Vec3
    t.goals = pathfinderPkg.goals
    // 止められたら待ちをやめる sleep と、終わりに消えるタイマー
    t.sleep = (ms) => new Promise((resolve, reject) => {
      if (stopped) return reject(new Error(stopped))
      const id = setTimeout(() => { owned.timers.delete(id); resolve() }, Number(ms))
      owned.timers.add(id)
    })
    const limit = setTimeout(() => stop(`ran longer than ${Math.round(limits.rawMs / 60000)} minutes`), limits.rawMs)
    const sandbox = vm.createContext({ __args: JSON.parse(JSON.stringify(args ?? {})), t })
    try {
      // 呼び出しも vm の中で行う: 最初の await までの同期の部分に cpuMs の時間切れが効く
      const fn = new vm.Script(`${wrap(skill.code)};\n__skill`).runInContext(sandbox, { timeout: limits.cpuMs })
      sandbox.__fn = fn
      const body = Promise.resolve(new vm.Script('__fn(t, __args)').runInContext(sandbox, { timeout: limits.cpuMs }))
      const out = await Promise.race([body, new Promise((resolve) => { rawStop = () => resolve({ __stopped: true }); if (stopped) rawStop() })])
      if (stopped) return { ended: 'stopped', reason: stopped }
      if (out?.__end === 'fail') return { ended: 'fail', reason: out.reason || 'the skill gave up' }
      return { ended: 'done', summary: out?.__end === 'done' ? out.summary : clip(out ?? '') }
    } catch (e) {
      if (stopped) return { ended: 'stopped', reason: stopped }
      if (/Script execution timed out/.test(e.message)) return { ended: 'stopped', reason: ENDLESS }
      return { ended: 'error', reason: clip(`${e.message}${e.stack ? ` @ ${String(e.stack).split('\n')[1]?.trim() ?? ''}` : ''}`, 300) }
    } finally {
      clearTimeout(limit)
    }
  }

  function finish (r) {
    return { ...r, calls: calls.slice(-MAX_CALLS_SHOWN), log, seconds: Math.round((Date.now() - started) / 100) / 10 }
  }
}

// 同じ理由の失敗がこの回数続いた版は、続きを動かさない（2026-10-08: 畑の技が 143 回のうち 140 回、同じ理由で失敗した）。
// 数えるのは技ごと: 間にほかの道具（持ち替えなど）が成功しても切れない。前に失敗してから SKILL_PROBE_MS たてば、
// 世界が変わったかもしれないので 1 回だけ試せる
export const SKILL_STREAK_LIMIT = 5
export const SKILL_PROBE_MS = 10 * 60 * 1000
// 失敗の理由が同じか: 数字（座標、個数、id）は無視する
const reasonKey = (reason) => String(reason ?? '').replace(/-?\d+(\.\d+)?/g, '#').slice(0, 200)

// 版が今、同じ理由で失敗し続けているか → 続いた回数（SKILL_STREAK_LIMIT 未満なら 0）
export const failingStreak = (v) => ((v?.streak ?? 0) >= SKILL_STREAK_LIMIT ? v.streak : 0)

// 動かさない理由（動かしてよければ null）。good は前に成功した版（なければ null）
export function blockedReason (skill, good, now = Date.now()) {
  const streak = failingStreak(skill)
  const sinceMs = now - new Date(skill.last_used ?? 0).getTime()
  if (!streak || sinceMs >= SKILL_PROBE_MS) return null
  return `${skill.name} v${skill.version} failed ${streak} times in a row for the same reason (${skill.last_failure}): running it again will not change the result. ` +
    `Read it (GET the skill) and write a revised version, ${good && good !== skill.version ? `run v${good}, which worked, ` : ''}or do the step with the other tools. ` +
    `It can be tried again ${Math.ceil((SKILL_PROBE_MS - sinceMs) / 60000)} min from now`
}

// 技の保存（1 つの技 = 1 つの JSON。版を積む）。ワールドのリセットの後も残す（設計書 22 §8 の 3）
export class SkillStore {
  constructor (dir = DEFAULT_DIR) {
    this.dir = dir
  }

  file (name) { return path.join(this.dir, `${name}.json`) }

  read (name) {
    try {
      return JSON.parse(fs.readFileSync(this.file(name), 'utf8'))
    } catch {
      return null
    }
  }

  write (skill) {
    fs.mkdirSync(this.dir, { recursive: true })
    const tmp = `${this.file(skill.name)}.tmp`
    fs.writeFileSync(tmp, JSON.stringify(skill, null, 2))
    fs.renameSync(tmp, this.file(skill.name))
  }

  names () {
    try {
      return fs.readdirSync(this.dir).filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5)).sort()
    } catch {
      return []
    }
  }

  // 新しい版を保存する → 版の番号
  put (name, def) {
    if (!NAME.test(name)) bad('the name must be snake_case: a-z, 0-9 and _, 2-40 characters')
    const v = validateSkill(def)
    const skill = this.read(name) ?? { name, created_at: new Date().toISOString(), versions: [] }
    const version = (skill.versions.at(-1)?.version ?? 0) + 1
    skill.versions.push({ version, ...v, created_at: new Date().toISOString(), uses: 0, successes: 0, failures: 0, streak: 0, last_failure: null })
    this.write(skill)
    return version
  }

  // 1 つの版（version なしなら一番新しい版: 書いたばかり・直したばかりの版を試す）
  get (name, version) {
    const skill = this.read(name)
    if (!skill) return null
    const v = version != null
      ? skill.versions.find((x) => x.version === Number(version))
      : skill.versions.at(-1)
    return v ? { name, ...v } : null
  }

  // 版を指定しないで動かすとき、動かす版（設計書 168 §4）。一番新しい版は、書いたばかり・直したばかりで
  // 1 回試す。その 1 回が失敗して一度も成功していないなら、前に成功した版の中で一番成功率が高いものに戻す
  // （新しい版が悪くなっても、動いていた版を使えなくしない）。戻すものがなければ null（一番新しい版）
  defaultVersion (name) {
    const skill = this.read(name)
    const latest = skill?.versions.at(-1)
    if (!latest || latest.uses === 0 || latest.successes > 0) return null
    const rate = (v) => v.successes / v.uses
    const best = skill.versions
      .filter((v) => v !== latest && v.successes > 0)
      .sort((a, b) => rate(b) - rate(a) || b.version - a.version)[0]
    return best ? { version: best.version, from: latest.version } : null
  }

  // 実行の結果を数える → 今回で初めて確かめ済みになったか
  record (name, version, ok, reason = null, seconds = 0) {
    const skill = this.read(name)
    const v = skill?.versions.find((x) => x.version === version)
    if (!v) return false
    const firstSuccess = ok && v.successes === 0
    v.uses += 1
    v.seconds_total = Math.round(((v.seconds_total ?? 0) + (Number(seconds) || 0)) * 10) / 10
    if (ok) {
      v.successes += 1
      v.streak = 0
    } else {
      v.failures += 1
      v.streak = reasonKey(reason) === reasonKey(v.last_failure) ? (v.streak ?? 0) + 1 : 1
      v.last_failure = reason
    }
    v.last_used = new Date().toISOString()
    this.write(skill)
    return firstSuccess
  }

  // 一覧（一番新しい版と、全部の版の合計）
  list () {
    return this.names().map((name) => this.read(name)).filter(Boolean).map((s) => {
      const latest = s.versions.at(-1)
      const sum = (k) => s.versions.reduce((n, v) => n + v[k], 0)
      const runs = this.defaultVersion(s.name)
      const active = runs ? s.versions.find((v) => v.version === runs.version) : latest
      return {
        name: s.name,
        description: latest.description,
        params: latest.params,
        expects: latest.expects,
        raw: latest.raw === true,
        version: latest.version,
        // 版を指定しないで動かす版（戻したときは前に成功した版）が、成功していて、同じ理由で失敗し続けていない
        verified: active.successes > 0 && !failingStreak(active),
        runs_version: active.version,
        versions: s.versions.map((v) => ({
          version: v.version,
          uses: v.uses,
          successes: v.successes,
          failures: v.failures,
          avg_seconds: v.uses ? Math.round(((v.seconds_total ?? 0) / v.uses) * 10) / 10 : null
        })),
        streak: latest.streak ?? 0,
        failing: failingStreak(latest) > 0,
        uses: sum('uses'),
        successes: sum('successes'),
        failures: sum('failures'),
        last_failure: latest.last_failure,
        // 一番新しい版がまだ成功していないとき、前に成功した版（run_skill の version で使える）
        last_good_version: [...s.versions].reverse().find((v) => v.successes > 0)?.version ?? null,
        last_used: s.versions.map((v) => v.last_used).filter(Boolean).sort().at(-1) ?? null
      }
    })
  }

  // 配信の外の掃除（設計書 22 §8 の 4「失敗作は消す」）: 一度も成功していない版を消す。成功した版が
  // 1 つもない技はファイルごと消す → { removed: [名前], pruned: [{ name, versions }] }
  clean ({ dryRun = false } = {}) {
    const removed = []
    const pruned = []
    for (const name of this.names()) {
      const skill = this.read(name)
      if (!skill) continue
      const drop = skill.versions.filter((v) => v.successes === 0).map((v) => v.version)
      if (drop.length === skill.versions.length) {
        removed.push(name)
        if (!dryRun) fs.rmSync(this.file(name))
        continue
      }
      if (!drop.length) continue
      pruned.push({ name, versions: drop })
      if (!dryRun) {
        skill.versions = skill.versions.filter((v) => !drop.includes(v.version))
        this.write(skill)
      }
    }
    return { removed, pruned }
  }
}

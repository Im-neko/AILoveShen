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
import ivm from 'isolated-vm'
import { ACTION_TOOLS, QUERY_TOOLS } from './tools.mjs'

export const LIMITS = {
  seconds: 180, // 1 回の実行の時間
  actions: 40, // 行動の道具の呼び出し
  queries: 200, // 調べもの・state()・block_at()
  judges: 10, // Jev への質問
  cpuMs: 1000, // isolate の中で使った CPU 時間（await せずに回るループを止める）
  memoryMb: 32,
  judgeWaitMs: 5000 // judge の答えを待つ時間
}
export const MAX_CODE_CHARS = 4000
export const MAX_DESCRIPTION_CHARS = 200
const MAX_LOG = 50
const MAX_CALLS_SHOWN = 30
const EXPECT_PREDICATES = ['have', 'stored', 'built', 'placed', 'lit', 'progress']
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
  const { description, params, expects, code } = def ?? {}
  if (typeof description !== 'string' || !description.trim()) bad('description is required')
  if (description.length > MAX_DESCRIPTION_CHARS) bad(`description is longer than ${MAX_DESCRIPTION_CHARS} characters`)
  if (params != null && (typeof params !== 'object' || Array.isArray(params))) bad('params must be a JSON Schema object')
  if (typeof code !== 'string' || !code.trim()) bad('code is required')
  if (code.length > MAX_CODE_CHARS) bad(`the code is longer than ${MAX_CODE_CHARS} characters`)
  validateExpects(expects)
  const isolate = new ivm.Isolate({ memoryLimit: 8 })
  try {
    isolate.compileScriptSync(wrap(code))
  } catch (e) {
    bad(`the code does not compile: ${e.message}`)
  } finally {
    isolate.dispose()
  }
  return { description: description.trim(), params: params ?? { type: 'object', properties: {} }, expects, code }
}

function validateExpects (e) {
  if (!e || typeof e !== 'object') bad(`expects is required: { predicate: ${EXPECT_PREDICATES.join(' | ')}, ... }`)
  if (!EXPECT_PREDICATES.includes(e.predicate)) bad(`expects.predicate must be one of ${EXPECT_PREDICATES.join(', ')}`)
  if (['have', 'stored'].includes(e.predicate)) {
    if (e.item == null) bad(`expects ${e.predicate} needs an item`)
    if (e.count == null) bad(`expects ${e.predicate} needs a count (a number, "+N" or "$param")`)
  }
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
  if (!['have', 'stored'].includes(expects.predicate)) return {}
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
  const isolate = new ivm.Isolate({ memoryLimit: limits.memoryMb })
  const judgeAbort = new AbortController()

  const stop = (reason) => {
    if (stopped) return
    stopped = reason
    judgeAbort.abort()
    if (current) deps.abortTool?.(reason)
    try { isolate.dispose() } catch {}
  }
  control.stop = stop
  control.view = () => ({ calls: used.action + used.query, current })

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
      if (name === 'state') return deps.state()
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

  const timer = setTimeout(() => stop(`time limit (${limits.seconds} s)`), limits.seconds * 1000)
  const cpu = setInterval(() => {
    try {
      if (Number(isolate.cpuTime) / 1e6 > limits.cpuMs) stop(ENDLESS)
    } catch {}
  }, 100)
  try {
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
    clearTimeout(timer)
    clearInterval(cpu)
    try { isolate.dispose() } catch {}
  }

  function finish (r) {
    return { ...r, calls: calls.slice(-MAX_CALLS_SHOWN), log, seconds: Math.round((Date.now() - started) / 100) / 10 }
  }
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
    skill.versions.push({ version, ...v, created_at: new Date().toISOString(), uses: 0, successes: 0, failures: 0, last_failure: null })
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

  // 実行の結果を数える → 今回で初めて確かめ済みになったか
  record (name, version, ok, reason = null) {
    const skill = this.read(name)
    const v = skill?.versions.find((x) => x.version === version)
    if (!v) return false
    const firstSuccess = ok && v.successes === 0
    v.uses += 1
    if (ok) v.successes += 1
    else { v.failures += 1; v.last_failure = reason }
    v.last_used = new Date().toISOString()
    this.write(skill)
    return firstSuccess
  }

  // 一覧（一番新しい版と、全部の版の合計）
  list () {
    return this.names().map((name) => this.read(name)).filter(Boolean).map((s) => {
      const latest = s.versions.at(-1)
      const sum = (k) => s.versions.reduce((n, v) => n + v[k], 0)
      return {
        name: s.name,
        description: latest.description,
        params: latest.params,
        expects: latest.expects,
        version: latest.version,
        verified: latest.successes > 0,
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

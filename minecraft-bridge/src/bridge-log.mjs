// ブリッジの標準出力・標準エラーを logs/bridge.log にも残す（設計書 58 の追記）。
//
// `[move] … 掘っては置く繰り返し` のような行は端末にしか出ず、端末を閉じると読めなかった（2026-10-01:
// go_home が 15 分同じ穴で止まった原因の確かめに、その行が要った）。console の出力を端末へ出したまま、
// 時刻をつけてファイルへ追記する。BRIDGE_LOG_FILE で場所を変えられ、空文字なら残さない。
// 起動のとき 20 MB を超えていたら bridge.log.1 に移して、新しく始める（1 世代だけ）。

import { appendFileSync, mkdirSync, renameSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { format } from 'node:util'

const DEFAULT_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'logs', 'bridge.log')
export const MAX_BYTES = 20 * 1024 * 1024
const LEVELS = ['log', 'info', 'warn', 'error', 'debug']

export function teeConsole (target = console, file = process.env.BRIDGE_LOG_FILE ?? DEFAULT_FILE, now = () => new Date()) {
  if (!file) return () => {}
  try {
    mkdirSync(path.dirname(file), { recursive: true })
    try {
      if (statSync(file).size > MAX_BYTES) renameSync(file, `${file}.1`)
    } catch {}
  } catch {
    return () => {}
  }
  const originals = {}
  let broken = false
  for (const level of LEVELS) {
    originals[level] = target[level]
    target[level] = (...args) => {
      originals[level].apply(target, args)
      if (broken) return
      try {
        appendFileSync(file, `${now().toISOString()} ${level} ${format(...args)}\n`)
      } catch {
        broken = true // 書けなくなっても、ブリッジは止めない
      }
    }
  }
  return () => { for (const level of LEVELS) target[level] = originals[level] }
}

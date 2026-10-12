// PRIMITIVES の同じ名前の手が 2 回書かれていないこと（後のものが先のものを黙って上書きする。
// 2026-10-12: 設計書 167 の変更で 570 行がそっくり重なり、新しい clear_former_home が古い方に上書きされていた）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('PRIMITIVES の手の名前は重ならない', () => {
  const src = readFileSync(new URL('../src/primitives.mjs', import.meta.url), 'utf8')
  const start = src.indexOf('export const PRIMITIVES = {')
  assert.ok(start >= 0)
  const names = [...src.slice(start).matchAll(/^ {2}(?:async )?([a-z_0-9]+) \(/gm)].map((m) => m[1]).filter((n) => !['if', 'for', 'while', 'switch'].includes(n))
  const seen = new Set()
  const dup = names.filter((n) => seen.has(n) || !seen.add(n))
  assert.deepEqual([...new Set(dup)], [])
})

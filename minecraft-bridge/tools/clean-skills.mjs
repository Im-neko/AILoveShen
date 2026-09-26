// 技の掃除（設計書 22 §8 の 4「失敗作は消す。配信外で掃除」）: 一度も成功していない版を消し、成功した版が
// 1 つもない技はファイルごと消す。配信中（ブリッジが動いている間）は動かさない。
//
//   npm run skills:clean            # 消す
//   npm run skills:clean -- --dry-run   # 何を消すかだけ表示
//   npm run skills:clean -- --force     # ブリッジが動いていても消す
import '../src/env.mjs'
import { SkillStore, DEFAULT_DIR } from '../src/skills.mjs'

const dryRun = process.argv.includes('--dry-run')
const force = process.argv.includes('--force')
const port = Number(process.env.BRIDGE_PORT ?? 3000)

if (!force && !dryRun) {
  const running = await fetch(`http://127.0.0.1:${port}/skills`, { signal: AbortSignal.timeout(1000) }).then(() => true, () => false)
  if (running) {
    console.log(`ブリッジが動いている（:${port}）。配信の外で、ブリッジを止めてから動かす（--force で無視）`)
    process.exit(1)
  }
}

const store = new SkillStore(process.env.SKILLS_DIR ?? DEFAULT_DIR)
const { removed, pruned } = store.clean({ dryRun })
const verb = dryRun ? '消す予定' : '消した'
for (const name of removed) console.log(`${verb}: ${name}（一度も成功していない）`)
for (const { name, versions } of pruned) console.log(`${verb}: ${name} の版 ${versions.join(', ')}（成功していない）`)
if (!removed.length && !pruned.length) console.log('消すものはない')
console.log(`残る技: ${store.list().filter((s) => !removed.includes(s.name)).length}`)

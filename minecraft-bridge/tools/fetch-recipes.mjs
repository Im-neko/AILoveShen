// バニラのレシピとアイテムのタグを取ってくる（設計書 29）。misode/mcmeta の要約（その版のゲームの
// データから作られたもの）。アイテム・ブロック・モブの日本語名も（ゲームの ja_jp.json から。検索で
// 日本語の検索語を引く: 設計書 47 §3）。バージョンを上げるときに取り直してコミットする:
//   npm run recipes:fetch            # 1.21.4
//   npm run recipes:fetch -- 1.21.5
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const version = process.argv[2] ?? '1.21.4'
const base = `https://raw.githubusercontent.com/misode/mcmeta/${version}-summary/data`
const out = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data-static')

for (const [what, file] of [['recipe', `recipes-${version}.json`], ['tag/item', `item-tags-${version}.json`]]) {
  const res = await fetch(`${base}/${what}/data.min.json`)
  if (!res.ok) throw new Error(`${what}: HTTP ${res.status}`)
  const data = await res.json()
  fs.mkdirSync(out, { recursive: true })
  fs.writeFileSync(path.join(out, file), JSON.stringify(data))
  console.log(`${file}: ${Object.keys(data).length} 件`)
}

// 日本語名: { items: {name: 日本語}（ブロックの名前も。アイテムの名前が先）, entities: {name: 日本語} }
const lang = await fetch(`https://raw.githubusercontent.com/misode/mcmeta/${version}-assets/assets/minecraft/lang/ja_jp.json`)
if (!lang.ok) throw new Error(`ja_jp: HTTP ${lang.status}`)
const ja = await lang.json()
const names = { items: {}, entities: {} }
for (const [key, text] of Object.entries(ja)) {
  const m = key.match(/^(item|block|entity)\.minecraft\.([a-z0-9_]+)$/)
  if (!m) continue
  if (m[1] === 'entity') names.entities[m[2]] = text
  else if (m[1] === 'item' || !names.items[m[2]]) names.items[m[2]] = text
}
fs.writeFileSync(path.join(out, `names-ja-${version}.json`), JSON.stringify(names))
console.log(`names-ja-${version}.json: ${Object.keys(names.items).length} 件、モブ ${Object.keys(names.entities).length} 件`)

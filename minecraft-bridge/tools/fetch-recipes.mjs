// バニラのレシピとアイテムのタグを取ってくる（設計書 29）。misode/mcmeta の要約（その版のゲームの
// データから作られたもの）。バージョンを上げるときに取り直してコミットする:
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

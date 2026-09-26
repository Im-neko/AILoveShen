// バニラのレシピ全部（設計書 29）: data-static の JSON（npm run recipes:fetch）を読み、タグを展開し、
// 計画（Knowledge / solver）と調べもの（recipe_of / find_recipes）に使える形にする。
//
// 材料は「どれか 1 つ」の候補の集まり（tag があればタグの名前も）。クラフトの実行はサーバーの
// レシピ本（craft.mjs）なので、ここは知識だけ。
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data-static')
const strip = (id) => String(id).replace(/^#/, '').replace(/^minecraft:/, '')

// レシピの種類 → 使う台（null: 手持ちでも、作業台でも）
const STATIONS = {
  crafting_shaped: 'crafting',
  crafting_shapeless: 'crafting',
  smelting: 'furnace',
  blasting: 'blast_furnace',
  smoking: 'smoker',
  campfire_cooking: 'campfire',
  stonecutting: 'stonecutter',
  smithing_transform: 'smithing_table'
}

export class RecipeIndex {
  constructor (recipes, tags) {
    this.tags = tags
    this.tagCache = new Map()
    this.byResult = new Map() // 結果のアイテム -> [レシピ]
    this.all = []
    for (const [id, raw] of Object.entries(recipes)) {
      const r = this.normalize(id, raw)
      if (!r) continue
      this.all.push(r)
      if (!this.byResult.has(r.result)) this.byResult.set(r.result, [])
      this.byResult.get(r.result).push(r)
    }
  }

  static load (version = '1.21.4', dir = DIR) {
    const read = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'))
    return new RecipeIndex(read(`recipes-${version}.json`), read(`item-tags-${version}.json`))
  }

  // タグのメンバー（入れ子のタグも展開）
  tagMembers (tag) {
    const name = strip(tag)
    if (this.tagCache.has(name)) return this.tagCache.get(name)
    this.tagCache.set(name, []) // 循環の見張り
    const out = []
    for (const v of this.tags[name]?.values ?? []) {
      const ref = typeof v === 'string' ? v : v.id
      if (ref.startsWith('#')) out.push(...this.tagMembers(ref))
      else out.push(strip(ref))
    }
    const members = [...new Set(out)]
    this.tagCache.set(name, members)
    return members
  }

  // 材料 1 つ（"minecraft:x" / "#minecraft:tag" / その配列）→ { alts, tag? }
  ingredient (raw) {
    if (Array.isArray(raw)) return { alts: [...new Set(raw.flatMap((x) => this.ingredient(x).alts))] }
    const s = typeof raw === 'string' ? raw : raw?.item ?? raw?.tag ?? ''
    if (s.startsWith('#') || raw?.tag) {
      const tag = strip(s)
      return { alts: this.tagMembers(tag), tag }
    }
    return { alts: [strip(s)] }
  }

  normalize (id, raw) {
    const type = strip(raw.type)
    const station = STATIONS[type]
    if (!station || !raw.result) return null
    const result = strip(raw.result.id ?? raw.result.item ?? raw.result)
    const count = raw.result.count ?? 1
    let ingredients = []
    let needsTable = false
    if (type === 'crafting_shaped') {
      const cells = raw.pattern.join('').split('').filter((c) => c !== ' ')
      const counts = {}
      for (const c of cells) counts[c] = (counts[c] ?? 0) + 1
      ingredients = Object.entries(counts).map(([c, n]) => ({ ...this.ingredient(raw.key[c]), count: n }))
      needsTable = raw.pattern.length > 2 || raw.pattern.some((row) => row.length > 2)
    } else if (type === 'crafting_shapeless') {
      const merged = new Map()
      for (const x of raw.ingredients) {
        const ing = this.ingredient(x)
        const key = ing.tag ?? ing.alts.join('|')
        merged.set(key, { ...ing, count: (merged.get(key)?.count ?? 0) + 1 })
      }
      ingredients = [...merged.values()]
      needsTable = raw.ingredients.length > 4
    } else if (type === 'smithing_transform') {
      ingredients = ['template', 'base', 'addition'].map((k) => ({ ...this.ingredient(raw[k]), count: 1 }))
    } else {
      ingredients = [{ ...this.ingredient(raw.ingredient), count: 1 }]
    }
    if (ingredients.some((i) => !i.alts.length)) return null
    return { id: strip(id), type, station, result, count, ingredients, needsTable }
  }

  recipesFor (item) {
    return this.byResult.get(item) ?? []
  }

  // 検索: 結果か材料の名前に、クエリの語がすべて入っているもの。結果の名前に入るものを先に
  search (query, limit = 8) {
    const words = String(query).toLowerCase().split(/[\s,]+/).filter(Boolean)
    if (!words.length) return []
    const scored = []
    for (const r of this.all) {
      const text = `${r.result} ${r.ingredients.map((i) => i.tag ?? i.alts.join(' ')).join(' ')}`
      if (!words.every((w) => text.includes(w))) continue
      const inResult = words.filter((w) => r.result.includes(w)).length
      scored.push({ r, score: inResult * 10 - r.ingredients.length })
    }
    return scored.sort((a, b) => b.score - a.score).slice(0, limit).map(({ r }) => describeRecipe(r))
  }
}

// 人（Gemini）に見せる形: 結果、個数、台、材料（どれか 1 つなら「a / b」かタグの名前）
export function describeRecipe (r) {
  const show = (i) => i.tag ? `any ${i.tag}` : i.alts.length > 3 ? `any of ${i.alts.slice(0, 3).join(' / ')} …` : i.alts.join(' / ')
  return {
    result: r.result,
    makes: r.count,
    station: r.station === 'crafting' ? (r.needsTable ? 'crafting_table' : 'inventory (2x2) or crafting_table') : r.station,
    ingredients: r.ingredients.map((i) => `${i.count} ${show(i)}`)
  }
}

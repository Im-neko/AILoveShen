// ソルバーのためのゲームの知識: アイテムのグループ、アイテムの入手方法、データの補正。
//
// レシピ、ブロックのドロップ、モブのドロップは minecraft-data から取る。ドロップのデータには注意がいる:
// - silkTouch の印がついた項目はエンチャントした道具が要るので、入手元にしない（ガラスは入手元がない）
// - 葉は棒とリンゴをまれにしか落とさないのに dropChance 1 になっているので、NATURAL_BLOCKS にある
//   自然のブロックだけを入手元に数える（葉は入っていない）
// - 羊は entityLoot に羊毛がない（色はエンティティのメタデータ）: EXTRA_MOB_DROPS で足す
// - 狩るのは HUNTABLE の動物だけ（ゾンビは食料に数えられる rotten_flesh を落とす。ウサギは
//   ボットより速く逃げる）
// - 染める: minecraft-data は染色のレシピを 1 通りしか載せない（青い羊毛は黒い羊毛＋青の染料）。
//   同じ種類からのレシピは外している（ベッドからベッドを作らない）ので、色つきの羊毛が作れなかった:
//   DYED で白い羊毛＋染料を足す。色つきのベッドはその色の羊毛 3 つから作る
// - 花（染料の材料）は自然のブロックに入れる（ヤグルマギクから青、ケシから赤…）
// 精錬は minecraft-data にない: SMELTING にかまどで作るものを並べる（アイテムごとに材料は1つ）。

// グループ: グループの必要はどのメンバーでも満たせる（例: 家の壁はどの板材でもよい）
const GROUP_PATTERNS = {
  planks: /_planks$/,
  log: /^(?!stripped_).*_log$/,
  door: /^(?!iron_).*_door$/,
  bed: /_bed$/,
  wool: /_wool$/,
  sword: /_sword$/
}
const FOOD_EXCLUDED = new Set(['rotten_flesh', 'spider_eye', 'poisonous_potato', 'pufferfish', 'suspicious_stew',
  'chorus_fruit', 'ominous_bottle'])

// ドロップのために掘ってよい、自然にあるブロック（プレイヤーの建てたものは含めない: 家の壁は
// 板材なので板材は入れない。原木は入れ、掘る候補からは家を除く）
const NATURAL_BLOCKS = [/^(?!stripped_).*_log$/, /^(stone|granite|diorite|andesite|deepslate|tuff)$/,
  /^(dirt|grass_block|coarse_dirt|podzol|sand|red_sand|gravel|clay)$/, /_ore$/, /^(sugar_cane|pumpkin|melon)$/,
  /^(dandelion|poppy|blue_orchid|allium|azure_bluet|(red|orange|white|pink)_tulip|oxeye_daisy|cornflower|lily_of_the_valley|sunflower|lilac|rose_bush|peony)$/]

// 染めた羊毛: 白い羊毛＋その色の染料（上の注意）
const DYED = /^(orange|magenta|light_blue|yellow|lime|pink|gray|light_gray|cyan|purple|blue|brown|green|red|black)_(wool)$/

// かまどが何（アイテムかグループ）から何を作るか。出力1つに材料1つ、それぞれ 10 秒
const SMELTING = {
  iron_ingot: 'raw_iron',
  charcoal: 'log',
  cooked_beef: 'beef',
  cooked_porkchop: 'porkchop',
  cooked_mutton: 'mutton',
  cooked_chicken: 'chicken'
}
// かまどが材料のアイテム（アイテム名）から作るもの。なければ null
export function smeltingProduct (input) {
  return Object.keys(SMELTING).find((out) => {
    const from = SMELTING[out]
    return from === input || GROUP_PATTERNS[from]?.test(input)
  }) ?? null
}

// 燃料1個で精錬できる数: 石炭と木炭は 8 個、板材と原木は 1.5 個
export const FUELS = [{ spec: 'coal', per: 8 }, { spec: 'charcoal', per: 8 }, { spec: 'planks', per: 1.5 }, { spec: 'log', per: 1.5 }]

export const HUNTABLE = new Set(['cow', 'pig', 'sheep', 'chicken', 'mooshroom'])
const EXTRA_MOB_DROPS = { sheep: ['white_wool'] }

// グループのメンバーを別のメンバーに変えるレシピ（羊毛やベッドの染色）は役に立たない
const sameGroup = (a, b) => Object.values(GROUP_PATTERNS).some((re) => re.test(a) && re.test(b))

export class Knowledge {
  constructor (md) {
    this.md = md
    this.blockSources = new Map() // アイテム -> [ブロック名]
    for (const loot of md.blockLootArray) {
      if (!NATURAL_BLOCKS.some((re) => re.test(loot.block))) continue
      for (const d of loot.drops) {
        if (d.silkTouch || !md.itemsByName[d.item]) continue
        push(this.blockSources, d.item, loot.block)
      }
    }
    this.mobSources = new Map() // アイテム -> [エンティティ名]
    for (const loot of md.entityLootArray) {
      if (!HUNTABLE.has(loot.entity)) continue
      for (const d of loot.drops) push(this.mobSources, d.item, loot.entity)
    }
    for (const [mob, items] of Object.entries(EXTRA_MOB_DROPS)) for (const item of items) push(this.mobSources, item, mob)
  }

  // 必要なもののアイテム: グループ名かアイテム名 -> { label, members: [アイテム名] }
  resolve (spec) {
    if (spec === 'food') {
      return { label: 'food', members: this.md.foodsArray.map((f) => f.name).filter((n) => !FOOD_EXCLUDED.has(n)) }
    }
    const re = GROUP_PATTERNS[spec]
    if (re) return { label: spec, members: this.md.itemsArray.map((i) => i.name).filter((n) => re.test(n)) }
    if (this.md.itemsByName[spec]) return { label: spec, members: [spec] }
    throw new Error(`unknown item or group: ${spec}`)
  }

  isMember (spec, name) {
    return this.resolve(spec).members.includes(name)
  }

  // item のクラフトのレシピ: [{ 結果の個数, 材料 {name: count}, needsTable }]
  recipes (item) {
    const id = this.md.itemsByName[item]?.id
    const out = []
    for (const r of this.md.recipes[id] ?? []) {
      const cells = r.inShape ? r.inShape.flat() : r.ingredients
      const ingredients = {}
      for (const c of cells) {
        if (c == null) continue
        const name = this.md.items[c]?.name
        if (!name) continue
        ingredients[name] = (ingredients[name] ?? 0) + 1
      }
      if (Object.keys(ingredients).some((n) => n === item || sameGroup(n, item))) continue
      const needsTable = r.inShape ? r.inShape.length > 2 || r.inShape.some((row) => row.length > 2) : cells.length > 4
      out.push({ count: r.result.count, ingredients, needsTable })
    }
    const dyed = item.match(DYED)
    if (dyed && this.md.itemsByName[`${dyed[1]}_dye`]) {
      out.push({ count: 1, ingredients: { [`white_${dyed[2]}`]: 1, [`${dyed[1]}_dye`]: 1 }, needsTable: false })
    }
    return out
  }

  // item（アイテムかグループ）のかまどの材料。なければ null
  smeltingInput (item) {
    return SMELTING[item] ?? null
  }

  // そのブロックを回収できる道具。素手でよければ null
  harvestTools (block) {
    const tools = this.md.blocksByName[block]?.harvestTools
    return tools ? Object.keys(tools).map((id) => this.md.items[id].name) : null
  }
}

function push (map, key, value) {
  if (!map.has(key)) map.set(key, [])
  if (!map.get(key).includes(value)) map.get(key).push(value)
}

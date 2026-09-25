// バニラのクライアントのレシピ本ボタンと同じく、レシピ本を通してクラフトする。
//
// Mineflayer の bot.craft() は、カーソルのクリックを予測して材料を動かす。クリックを待たずに
// 続けて送るので、このサーバーでは最初の再同期のあとインベントリのモデルがずれ、後のクリックで
// アイテムがカーソルに入れ替わる。するとクラフトのたびに材料のスタックの残りを失う
// （原木 5 → 板材 4、原木 0）。
//
// ここでは、サーバー自身がクラフトのグリッドを埋め（craft_recipe_request）、結果はシフトクリックで
// 取り（サーバー側のクイック移動で、カーソルを使わない）、インベントリを読み直す前にウィンドウを
// サーバーから再同期する。

import { once } from 'node:events'

const RESULT_SLOT = 0
const GRID_FILL_TIMEOUT_MS = 2000
const WINDOW_OPEN_TIMEOUT_MS = 3000
// Paper は recipe-spam-limit（20、spigot.yml）を超えたレシピ要求を黙って捨てる。この枠は1ティックに
// 1 ずつ回復する。原木 19 個を1つずつクラフトすると 0.1 秒かかり、次の要求（作業台）が2回捨てられた。
// 2ティックに1要求なら枠に十分収まる。
const RECIPE_REQUEST_INTERVAL_MS = 100
let lastRecipeRequestAt = 0

export class RecipeBook {
  constructor (bot) {
    this.bot = bot
    this.byResult = new Map() // 結果のアイテム id -> Map(displayId -> display)
    bot._client.on('recipe_book_add', (packet) => {
      if (packet.replace) this.byResult.clear()
      for (const { recipe } of packet.entries) {
        const resultId = recipe.display?.data?.result?.data?.itemId
        if (resultId == null) continue
        if (!this.byResult.has(resultId)) this.byResult.set(resultId, new Map())
        this.byResult.get(resultId).set(recipe.displayId, recipe.display)
      }
    })
    bot._client.on('recipe_book_remove', ({ recipeIds }) => {
      for (const recipes of this.byResult.values()) for (const id of recipeIds) recipes.delete(id)
    })
  }

  // プレイヤーが解放済みで itemName を作るレシピ: [{ displayId, fitsInventory }]
  recipesFor (itemName) {
    const item = this.bot.registry.itemsByName[itemName]
    const recipes = item && this.byResult.get(item.id)
    if (!recipes) return []
    return [...recipes].map(([displayId, display]) => ({ displayId, fitsInventory: fits2x2(display) }))
  }
}

function fits2x2 (display) {
  if (display.type === 'crafting_shaped') return display.data.width <= 2 && display.data.height <= 2
  if (display.type === 'crafting_shapeless') return display.data.ingredients.length <= 4
  return false
}

// サーバーが結果スロットに itemName を入れたら解決する。タイムアウトなら false。
function waitForResult (window, itemName) {
  return new Promise((resolve) => {
    const onUpdate = (_old, item) => {
      if (item?.name !== itemName) return
      clearTimeout(timer)
      window.off(`updateSlot:${RESULT_SLOT}`, onUpdate)
      resolve(true)
    }
    const timer = setTimeout(() => { window.off(`updateSlot:${RESULT_SLOT}`, onUpdate); resolve(false) }, GRID_FILL_TIMEOUT_MS)
    window.on(`updateSlot:${RESULT_SLOT}`, onUpdate)
  })
}

// 2x2 のグリッドやカーソルに残ったものを（インベントリ画面を閉じるように）インベントリに戻し、
// 次のステップがサーバーの実際の状態から始まるよう再同期する。
async function resetInventory (bot) {
  bot._client.write('close_window', { windowId: 0 })
  await bot._syncWindow(bot.inventory)
}

async function openTable (bot, tableBlock) {
  const opened = once(bot, 'windowOpen', { signal: AbortSignal.timeout(WINDOW_OPEN_TIMEOUT_MS) })
  bot.activateBlock(tableBlock)
  const [window] = await opened
  return window
}

// itemName をクラフトする: makeAll はインベントリが許すだけのセットでグリッドを埋める（バニラで
// レシピをシフトクリックするのと同じ）。増えた個数を返す。
export async function craftWithRecipeBook (bot, book, itemName, { table = null, makeAll = false } = {}) {
  const recipes = book.recipesFor(itemName).filter((r) => table || r.fitsInventory)
  if (recipes.length === 0) throw new Error(`no unlocked recipe for ${itemName}${table ? '' : ' in the 2x2 grid'}`)
  const count = () => bot.inventory.items().filter((i) => i.name === itemName).reduce((n, i) => n + i.count, 0)
  await resetInventory(bot)
  const before = count()
  const window = table ? await openTable(bot, table) : bot.inventory
  try {
    for (const recipe of recipes) {
      const wait = RECIPE_REQUEST_INTERVAL_MS - (Date.now() - lastRecipeRequestAt)
      if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait))
      lastRecipeRequestAt = Date.now()
      const filled = waitForResult(window, itemName)
      bot._client.write('craft_recipe_request', { windowId: window.id, recipeId: recipe.displayId, makeAll })
      if (!(await filled)) continue // このレシピの変種には材料が足りない
      await bot.clickWindow(RESULT_SLOT, 0, 1)
      await bot._syncWindow(window)
      break
    }
  } finally {
    if (table) bot.closeWindow(window)
    await resetInventory(bot)
  }
  const gained = count() - before
  if (gained <= 0) throw new Error(`crafting ${itemName} produced nothing (missing ingredients?)`)
  return gained
}

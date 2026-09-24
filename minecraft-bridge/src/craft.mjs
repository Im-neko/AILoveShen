// Crafting through the recipe book, like the vanilla client's recipe book button.
//
// Mineflayer's bot.craft() moves ingredients with predicted cursor clicks. It sends them
// back to back without waiting, so on this server its inventory model drifts after the
// first resync and a later click swaps items onto the cursor. Each craft then loses the
// rest of the ingredient stack (5 logs -> 4 planks, 0 logs).
//
// Here the server fills the crafting grid itself (craft_recipe_request), the result is
// taken with a shift-click (server-side quick move, no cursor involved), and the window
// is resynced from the server before the inventory is read again.

import { once } from 'node:events'

const RESULT_SLOT = 0
const GRID_FILL_TIMEOUT_MS = 2000
const WINDOW_OPEN_TIMEOUT_MS = 3000

export class RecipeBook {
  constructor (bot) {
    this.bot = bot
    this.byResult = new Map() // result item id -> Map(displayId -> display)
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

  // Recipes the player has unlocked that produce itemName: [{ displayId, fitsInventory }]
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

// Resolves when the server puts itemName into the result slot, false on timeout.
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

// Returns anything left in the 2x2 grid or on the cursor to the inventory (like closing the
// inventory screen), then resyncs so the next step starts from the server's real state.
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

// Crafts itemName: makeAll fills the grid with as many sets as the inventory allows (vanilla
// shift-click on a recipe). Returns how many of the item were gained.
export async function craftWithRecipeBook (bot, book, itemName, { table = null, makeAll = false } = {}) {
  const recipes = book.recipesFor(itemName).filter((r) => table || r.fitsInventory)
  if (recipes.length === 0) throw new Error(`no unlocked recipe for ${itemName}${table ? '' : ' in the 2x2 grid'}`)
  const count = () => bot.inventory.items().filter((i) => i.name === itemName).reduce((n, i) => n + i.count, 0)
  await resetInventory(bot)
  const before = count()
  const window = table ? await openTable(bot, table) : bot.inventory
  try {
    for (const recipe of recipes) {
      const filled = waitForResult(window, itemName)
      bot._client.write('craft_recipe_request', { windowId: window.id, recipeId: recipe.displayId, makeAll })
      if (!(await filled)) continue // ingredients missing for this recipe variant
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

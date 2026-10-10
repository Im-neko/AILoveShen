// 置くときに寄りかかる面（GoalPlaceBlock の faces）。右クリックで開く・使うブロック（ドア・チェスト・作業台など）には
// 寄りかからない: その面に置こうとすると、置く代わりに開閉してしまう（2026-10-10 21:2x: 戸口の真上のマスに板を置くとき、
// 下のドアを基準にして、サーバーに断られながらドアを開け閉めし続けた。55 回）。
import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg

// 面を借りられないブロック（右クリックで開く・使う: 置く代わりに開いてしまう）
export const INTERACTIVE = /(chest|barrel|crafting_table|furnace|smoker|_door|_trapdoor|_fence_gate|_bed|anvil|table|lectern|loom|stonecutter|grindstone|hopper|dispenser|dropper|button|lever|shulker_box|note_block|jukebox|bell|beacon|brewing_stand|enchanting_table|cauldron|composter)$/

// pathfinder の既定と同じ順（下、上、北、南、西、東）
export const PLACE_FACES = [new Vec3(0, -1, 0), new Vec3(0, 1, 0), new Vec3(0, 0, -1), new Vec3(0, 0, 1), new Vec3(-1, 0, 0), new Vec3(1, 0, 0)]

// pos に置くときに、寄りかかってよい面（隣が使うブロックでないもの）
export function placeFaces (bot, pos) {
  return PLACE_FACES.filter((face) => {
    const near = bot.blockAt(pos.plus(face))
    return !(near && INTERACTIVE.test(near.name))
  })
}

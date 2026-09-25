// 経路移動の入口。行動の中断（signal）を移動まで届ける。
//
// 動いている最中の移動は、行動の中断が pathfinder の目標を消して止める。止められないのは、中断の
// 後に新しく始まる移動だった（掘った後に落とし物を拾いに行く移動が、中断の後に始まり、行き着けない
// まま終わらなかった: town4）。なので、移動はここからだけ始め、始める前に中断を確かめる。

export async function walkTo (bot, goal, signal) {
  signal.throwIfAborted()
  try {
    await bot.pathfinder.goto(goal)
  } finally {
    bot.pathfinder.setGoal(null)
  }
}

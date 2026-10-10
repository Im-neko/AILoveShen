// 採掘場へ持っていく松明の数（2026-10-10: 松明はチェストにあるのに、手持ち数本で採掘場へ行こうとした）。
// 坑道は 6 セルごとに壁へ 1 本（primitives.mjs の MINE_TORCH）。切れると掘る手が止まって取りに戻るので、
// 残りのセルぶんの 1.5 倍と予備を、手に持ってから下りる。下りる前に足りなければ、チェストから出すか作る（ソルバー）。
export const MINE_TORCH_EVERY = 6
export const MINE_TORCHES = { min: 16, max: 64, spare: 8 }

// 残り remaining セルを掘るために、手に持っておく松明の数
export function mineTorchesWanted (remaining) {
  const cells = Math.max(0, Number(remaining) || 0)
  const n = Math.ceil((cells / MINE_TORCH_EVERY) * 1.5) + MINE_TORCHES.spare
  return Math.max(MINE_TORCHES.min, Math.min(MINE_TORCHES.max, n))
}

// これだけ持っていれば、足りないぶんを作る手を並べたまま下りてよい（半分）
export const enoughToGo = (wanted) => Math.ceil(wanted / 2)

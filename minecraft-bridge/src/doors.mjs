// ドアと柵のゲートを通って歩く（2026-09-27: 家以外の建物に一度入ると出られなかった）。
//
// mineflayer-pathfinder は通れるセルを boundingBox が 'empty' のものだけにする。ドアは開いていても
// 'block' なので「壊して通るもの」になり、configureMovements の壊してよいもの（地形と葉だけ）に
// 入らないので通れなかった。canOpenDoors は柵のゲートしか開けず（しかも今の開き具合を見ずに切り替える）、
// ドアには効いていなかった。
//
// なので: 木のドアと柵のゲートは、その向き（facing）に沿ってまっすぐ入るときだけ通れるセルにする
// （斜め・上下の移動は今まで通り通れない: 開いたドアの板にぶつかる）。開けるのは歩いている間の
// doorOpener: 経路の次の数ノードに閉じたドアがあれば開け、自分で開けたドアは通り過ぎたら閉める。
// 家と前の家のドアは今まで通りコードが出入りを受け持つ（home.mjs の enterHome / leaveHome: 夜に
// 閉じこもる、ドアの前の敵を見る）ので、経路には入れない。鉄のドアは手で開かないので通れない。

import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg

const PASSABLE = /^(?!iron_)\w+_(door|fence_gate)$/
const OPEN_REACH_M = 3 // ドアにここまで近づいたら開ける（歩きながら、ぶつかる前に）
const PASSED_M = 1.5 // 自分で開けたドアから、これだけ離れたら閉める
const CLOSE_REACH_M = 4 // これより離れたら閉められない（開けたままにする）
const LOOK_AHEAD = 3 // 経路の先の何ノードまで見るか
const TICK_MS = 100
const CONFIRM_TICKS = 10 // 開け閉めがサーバーから返ってくるのを待つ（その前に見ると、まだ前の状態で切り替え直す）

const key = (p) => `${p.x},${p.y},${p.z}`

export const isDoorLike = (name) => PASSABLE.test(name ?? '')

// ドアの下半分の位置（ゲートはそのまま）
const lowerHalf = (b) => b.getProperties?.().half === 'upper' ? b.position.offset(0, -1, 0) : b.position

// 家・前の家のドア（上下どちらの半分も）: コードが出入りする
export function isCodeManagedDoor (state, pos) {
  const homes = [state.home, ...(state.formerHomes ?? [])].filter(Boolean)
  return homes.some((h) => h.door && h.door.x === pos.x && h.door.z === pos.z && (pos.y === h.door.y || pos.y === h.door.y + 1))
}

// 経路が通ってよいドア・ゲートか
export function passableDoor (state, block) {
  return !!block?.position && isDoorLike(block.name) && !isCodeManagedDoor(state, block.position)
}

// ドアの向きに沿った動きか（北・南向きのドアは南北に通る）
function alongDoor (block, dir) {
  const facing = block.getProperties?.().facing
  if (facing === 'north' || facing === 'south') return dir.x === 0 && dir.z !== 0
  if (facing === 'east' || facing === 'west') return dir.z === 0 && dir.x !== 0
  return false
}

// Movements に、まっすぐ入るときだけドアを通れるセルとして教える。
// isPassableDoor は doorOpener が読む（どのドアを開けてよいか）
export function teachDoors (m, state) {
  const getBlock = m.getBlock
  const getMoveForward = m.getMoveForward
  let dir = null
  m.isPassableDoor = (block) => passableDoor(state, block)
  m.getMoveForward = function (node, d, neighbors) {
    dir = d
    try {
      return getMoveForward.call(this, node, d, neighbors)
    } finally {
      dir = null
    }
  }
  m.getBlock = function (pos, dx, dy, dz) {
    const b = getBlock.call(this, pos, dx, dy, dz)
    if (dir && passableDoor(state, b) && alongDoor(b, dir)) {
      b.safe = true
      b.physical = false
      b.openable = false // pathfinder の開け方（今の開き具合を見ずに切り替える）は使わない
    }
    return b
  }
}

// 歩いている間、経路の先の閉じたドアを開け、自分で開けたものは通り過ぎたら閉める。
// 戻り値の stop() で見張りをやめ、まだ開けたままの届くドアを閉める
//
// 経路の直し: pathfinder は経路の各ノードを、そのセルのブロックの「上」に立つ位置にする
// （postProcessPath の getPositionOnTopOf。ハーフブロックやカーペットのため）。ドアのセルでは扉の板の
// 上（y+1、板の真ん中の x）になり、ボットがドアに登ろうとして止まった（実機: 2026-09-27）。
// path_update は直した直後に同期で出る（動くのはその後）ので、ここでドアのノードを床の上のセルの
// 真ん中に戻す
export function doorOpener (bot) {
  if (!bot.on || !bot.blockAt) return { stop: async () => {} }
  const passable = (b) => !!bot.pathfinder?.movements?.isPassableDoor?.(b)
  let path = []
  const opened = new Map()
  let busy = null
  const onPath = (r) => {
    path = r?.path ?? []
    for (const n of path) {
      const cell = new Vec3(Math.floor(n.x), Math.floor(n.y) - 1, Math.floor(n.z))
      const b = bot.blockAt(cell)
      if (!b || !passable(b) || b.getProperties?.().half === 'upper' || n.y - cell.y < 0.9) continue
      n.x = cell.x + 0.5
      n.y = cell.y
      n.z = cell.z + 0.5
    }
  }
  const me = () => bot.entity.position
  const distance = (p) => me().distanceTo(p.offset(0.5, 0.5, 0.5))
  const inCell = (p) => Math.floor(me().x) === p.x && Math.floor(me().z) === p.z
  const ahead = (p) => path.slice(0, LOOK_AHEAD).some((n) => Math.floor(n.x) === p.x && Math.floor(n.z) === p.z)
  const isOpen = (b) => b.getProperties().open === true

  // 切り替えて、そうなったのを見るまで待つ（実機: 返ってくる前の次のティックでもう一度開け、3 回切り替えた）
  const toggle = async (b, open) => {
    await bot.activateBlock(b)
    for (let i = 0; i < CONFIRM_TICKS; i++) {
      const now = bot.blockAt(b.position)
      if (now && isDoorLike(now.name) && isOpen(now) === open) return
      await bot.waitForTicks(1)
    }
  }
  const openAhead = async () => {
    for (const n of path.slice(0, LOOK_AHEAD)) {
      for (const dy of [0, 1]) {
        const b = bot.blockAt(new Vec3(n.x, n.y + dy, n.z))
        if (!b || !passable(b) || isOpen(b) || distance(b.position) > OPEN_REACH_M) continue
        const lower = lowerHalf(b)
        await toggle(b, true)
        opened.set(key(lower), { pos: lower, passed: false })
        console.log(`[move] ドアを開けた: ${b.name} ${key(lower)}`)
      }
    }
  }
  // 通り抜けた（ドアのセルに入って出た）ものを閉める。all: 歩き終わったので、通らなかったものも閉める
  const closeBehind = async (all = false) => {
    for (const [k, door] of opened) {
      const p = door.pos
      if (inCell(p)) {
        door.passed = true
        continue
      }
      const d = distance(p)
      if (!all && (!door.passed || ahead(p) || d < PASSED_M)) continue
      opened.delete(k)
      const b = bot.blockAt(p)
      if (d > CLOSE_REACH_M || !b || !isDoorLike(b.name) || !isOpen(b)) continue
      await toggle(b, false)
      console.log(`[move] 通ったドアを閉めた: ${b.name} ${k}`)
    }
  }
  const tick = () => {
    if (busy || !bot.entity) return busy
    const run = (async () => {
      try {
        await openAhead()
        await closeBehind()
      } catch (e) {
        console.log(`[move] ドアを開け閉めできなかった: ${e.message}`)
      }
    })()
    busy = run
    run.finally(() => { if (busy === run) busy = null })
    return run
  }
  bot.on('path_update', onPath)
  const timer = setInterval(tick, TICK_MS)
  return {
    opened,
    tick,
    async stop () {
      clearInterval(timer)
      bot.off?.('path_update', onPath)
      await busy
      path = []
      await closeBehind(true).catch((e) => console.log(`[move] 通ったドアを閉められなかった: ${e.message}`))
    }
  }
}

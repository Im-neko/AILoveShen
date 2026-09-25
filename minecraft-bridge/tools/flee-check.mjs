// 別のボットで逃走を再現する: すぐ隣にゾンビを召喚し、距離と pathfinder の状態を追う。
// docker 経由で RCON を使う（開発用サーバーのコンテナ）。
import { execFileSync } from 'node:child_process'
import mineflayer from 'mineflayer'
import pathfinderPkg from 'mineflayer-pathfinder'
import { configureMovements, flee } from '../src/primitives.mjs'
import { round } from '../src/observe.mjs'

const rcon = (cmd) => execFileSync('docker', ['exec', 'ailoveshen-minecraft', 'rcon-cli', cmd]).toString().trim()
const name = 'FleeCheck'
const bot = mineflayer.createBot({ host: 'localhost', port: 25565, username: name, version: '1.21.4', auth: 'offline' })
bot.loadPlugin(pathfinderPkg.pathfinder)
const state = { plan: null }
bot.once('spawn', async () => {
  configureMovements(bot, state)
  rcon(`gamemode survival ${name}`)
  rcon(`effect give ${name} minecraft:resistance 60 4`) // テスト中に死なないように
  await bot.waitForTicks(40)
  rcon(`execute at ${name} run summon minecraft:zombie ^ ^ ^2 {Tags:["probe"],ArmorItems:[{},{},{},{id:"minecraft:leather_helmet",count:1}]}`)
  await bot.waitForTicks(20)
  bot.on('path_update', (r) => console.log(`[path_update] ${r.status} len=${r.path.length}`))
  bot.on('goal_reached', () => console.log('[goal_reached]'))
  bot.on('path_stop', () => console.log('[path_stop]'))
  const z = Object.values(bot.entities).find((e) => e.name === 'zombie' && e.position.distanceTo(bot.entity.position) < 6)
  const trace = setInterval(() => {
    if (z) console.log(`t dist=${round(z.position.distanceTo(bot.entity.position))} me=${bot.entity.position.floored()} vel=${round(bot.entity.velocity.x, 2)},${round(bot.entity.velocity.z, 2)} sprint=${bot.getControlState('sprint')} fwd=${bot.getControlState('forward')}`)
  }, 500)
  const signal = new AbortController().signal
  try {
    console.log('逃走:', await flee(bot, z, signal))
  } catch (e) { console.log('逃走に失敗:', e.message) }
  clearInterval(trace)
  rcon('kill @e[type=minecraft:zombie,tag=probe]')
  bot.quit()
})

// Headless check: connect to the mirror like a real client and report what arrives.
import mc from 'minecraft-protocol'

const port = Number(process.env.MIRROR_PORT ?? 25578)
const seconds = Number(process.argv[2] ?? 8)
const client = mc.createClient({ host: '127.0.0.1', port, username: 'ViewerCheck', version: '1.21.4', auth: 'offline' })
const counts = {}
const configNames = []
let login = null
let lastPos = null
const positions = []
let health = null
client.on('packet', (data, meta) => {
  if (meta.state === 'configuration') configNames.push(meta.name)
  if (meta.state !== 'play') return
  counts[meta.name] = (counts[meta.name] ?? 0) + 1
  if (meta.name === 'login') login = data
  if (meta.name === 'position') { lastPos = data; positions.push([Date.now(), data.x, data.z, data.yaw]); client.write('teleport_confirm', { teleportId: data.teleportId }) }
  if (meta.name === 'update_health') health = data
})
client.on('error', (e) => console.log('ERROR', e.message))
client.on('end', (r) => console.log('END', r))
setTimeout(() => {
  const moved = positions.length > 1 && positions.some(p => p[1] !== positions[0][1] || p[2] !== positions[0][2] || p[3] !== positions[0][3])
  console.log(JSON.stringify({
    configPackets: configNames.reduce((a, n) => (a[n] = (a[n] ?? 0) + 1, a), {}),
    reachedPlay: !!login,
    login: login && { entityId: login.entityId, dimension: login.worldState?.name, gamemode: login.worldState?.gamemode },
    digCrack: counts.block_break_animation ?? 0, armSwing: counts.animation ?? 0,
    chunks: counts.map_chunk ?? 0, light: counts.update_light ?? 0,
    health, positions: positions.length, moved,
    lastPos: lastPos && { x: lastPos.x.toFixed(2), y: lastPos.y.toFixed(2), z: lastPos.z.toFixed(2), yaw: lastPos.yaw.toFixed(1) },
    otherPlay: Object.fromEntries(Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 25))
  }, null, 1))
  process.exit(0)
}, seconds * 1000)

// テスト用: 決まった家の計画（壁は1層ずつ、屋根は外側から内側へ、ドアは最後）。
// 本当の計画は Python 側（HouseBlueprint）から来る。これは実行側を動かしてみるためだけのもの。
const [w, d, h] = (process.argv[2] ?? '5x5x3').split('x').map(Number)
const blocks = []
const doorX = Math.floor(w / 2)
const isDoor = (x, y, z) => z === 0 && x === doorX && y < 2
for (let y = 0; y < h; y++) {
  for (let x = 0; x < w; x++) {
    for (let z = 0; z < d; z++) {
      const edge = x === 0 || z === 0 || x === w - 1 || z === d - 1
      if (edge && !isDoor(x, y, z)) blocks.push({ x, y, z, block: 'planks' })
    }
  }
}
for (let ring = 0; ring * 2 < Math.min(w, d); ring++) {
  for (let x = ring; x < w - ring; x++) {
    for (let z = ring; z < d - ring; z++) {
      if (x === ring || z === ring || x === w - 1 - ring || z === d - 1 - ring) blocks.push({ x, y: h, z, block: 'planks' })
    }
  }
}
blocks.push({ x: doorX, y: 0, z: 0, block: 'door' })
console.log(JSON.stringify({ blocks, width: w, depth: d, height: h + 1 }))

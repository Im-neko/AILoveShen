// テスト用の、光の値を返す世界。light.mjs が読む形（本物の prismarine-chunk と同じ、1 バイトに 2 マスで、
// x が 16 マスごとに入れ替わった欄）で返すので、light.mjs が戻した値が lightAt(x, y, z) になる
import { mirrorX } from '../src/light.mjs'

const sections = (at, cx, cz, minY) => Array.from({ length: 26 }, (_, si) => ({
  get: (i) => at(cx + mirrorX(i & 15), minY + (si - 1) * 16 + (i >> 8), cz + ((i >> 4) & 15))
}))

export function lightWorld (lightAt, skyAt = null, minY = -64) {
  return {
    raycast: () => null,
    getColumnAt: (p) => {
      const cx = Math.floor(p.x / 16) * 16
      const cz = Math.floor(p.z / 16) * 16
      return {
        minY,
        blockLightSections: sections(lightAt, cx, cz, minY),
        ...(skyAt ? { skyLightSections: sections(skyAt, cx, cz, minY) } : {})
      }
    }
  }
}

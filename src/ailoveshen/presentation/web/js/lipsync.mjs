// 口の動き（アバターのページ avatar.html が読む）。本文から母音を並べ、前後の音を滑らかにつなぐ（設計書 24）。
// 純粋な関数だけ（画面に触らない）。テスト: tests/unit/presentation/web/test_lipsync.py

export const OPEN = ['aa', 'oh', 'ih', 'ee', 'ou']

// 見た目の調整つまみ
export const MOUTH = {
  dip: 0.12, // 音の境目で口が少し狭まる割合（0: 同じ母音が続くあいだ開きっぱなし、1: 境目で閉じる。以前は 0.45）
  mergeBelow: 0.12, // 1 音がこの秒数より短いとき、隣り合う 2 音を 1 つにまとめて遅くする
  smooth: 26 // 最後にかける追従の速さ（1 秒あたり。大きいほど速く追う。小さすぎると遅れる）
}

const VOWEL = {}
const rows = { aa: 'あかさたなはまやらわがざだばぱぁゃゎ', ih: 'いきしちにひみりぎじぢびぴぃ', ou: 'うくすつぬふむゆるぐずづぶぷぅゅゔ', ee: 'えけせてねへめれげぜでべぺぇ', oh: 'おこそとのほもよろをごぞどぼぽぉょ' }
for (const [v, chars] of Object.entries(rows)) {
  for (const c of chars) {
    VOWEL[c] = v
    VOWEL[String.fromCharCode(c.charCodeAt(0) + 0x60)] = v // カタカナ
  }
}
const SMALL = new Set('ゃゅょぁぃぅぇぉャュョァィゥェォ')
const PAUSE = /[、。，．,.!！?？…\s「」『』（）()]/

// 本文 → [{v, w}]（1 つがおよそ 1 モーラ。v が null は口を閉じる間）
export function visemes (text) {
  const out = []
  for (const c of text) {
    if (PAUSE.test(c)) { out.push({ v: null, w: 0 }, { v: null, w: 0 }); continue }
    if (c === 'ー' || c === '〜') { if (out.length) out.push({ ...out[out.length - 1] }); continue }
    if (c === 'っ' || c === 'ッ') { out.push({ v: null, w: 0 }); continue }
    if (c === 'ん' || c === 'ン') { out.push({ v: 'ou', w: 0.25 }); continue }
    if (SMALL.has(c) && out.length) { out[out.length - 1] = { v: VOWEL[c], w: 0.9 }; continue }
    if (VOWEL[c]) { out.push({ v: VOWEL[c], w: 0.9 }); continue }
    // 漢字や英数字: 読みが分からないので、字ごとに決まった 2 モーラ
    const code = c.codePointAt(0)
    const n = /[一-鿿]/.test(c) ? 2 : 1
    for (let i = 0; i < n; i++) out.push({ v: OPEN[(code + i * 3) % OPEN.length], w: 0.75 })
  }
  return out
}

// 隣り合う 2 音を 1 つにする。両方が声なら大きいほう（同じなら後ろ）、片方が間なら声のほう、両方が間なら間
function mergePairs (seq) {
  const out = []
  for (let i = 0; i < seq.length; i += 2) {
    const a = seq[i]
    const b = seq[i + 1]
    if (!b) out.push(a)
    else if (!a.v && !b.v) out.push(a)
    else if (!a.v) out.push(b)
    else if (!b.v) out.push(a)
    else out.push(a.w > b.w ? a : b)
  }
  return out
}

// 速すぎる（1 音が mergeBelow 秒より短い）あいだ、2 音を 1 つにして遅くする。人の口は早口では全部の音を追わない
export function pace (seq, per, mergeBelow = MOUTH.mergeBelow) {
  let s = seq
  let p = per
  while (p < mergeBelow && s.length > 1) {
    s = mergePairs(s)
    p *= 2
  }
  return { seq: s, per: p }
}

const smoothstep = (x) => x * x * (3 - 2 * x)

// 経過した音の数 k（小数）での各母音の開き。各音を中央（i + 0.5）に置いたキーフレームにして、間を滑らかな曲線でつなぐ
// （前後の音をまたぐあいだ、片方が閉じる分だけ次が開く。以前は 1 音ごとに山を作り、境目で必ず閉じかけていた）
export function mouthAt (seq, k, dip = MOUTH.dip) {
  const out = Object.fromEntries(OPEN.map((v) => [v, 0]))
  if (!seq.length || k < 0 || k > seq.length) return out
  const x = k - 0.5
  const i0 = Math.floor(x)
  const frac = x - i0
  const weight = (i, v) => (i >= 0 && i < seq.length && seq[i].v === v ? seq[i].w : 0)
  const wobble = 1 - dip * Math.sin(Math.PI * frac) ** 2 // 境目（frac = 0.5）でわずかに狭まる
  const edge = smoothstep(Math.min(1, Math.min(k, seq.length - k) / 0.5)) // 話し始めと終わりは、口を閉じた状態からつなぐ
  const s = smoothstep(frac)
  for (const v of OPEN) {
    const a = weight(i0, v)
    const b = weight(i0 + 1, v)
    out[v] = (a + (b - a) * s) * wobble * edge
  }
  return out
}

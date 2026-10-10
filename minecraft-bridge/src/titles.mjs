// 建物と施設の呼び名（視聴者に見せ、配信者が話す名前）。識別子（workshop、wheat_farm）は判定に使うので変えず、
// 呼び名を別に持つ（docs/design/160_stream_names.md）。呼び名は建物の設計 plan.design.title と施設 fac.title に
// 置く: どちらも state.json に保存され、ワールドのリセットで一緒に退く。
export const MAX_TITLE = 24

// { 識別子: 呼び名 }（呼び名のあるものだけ）
export function titlesOf (state) {
  const out = {}
  for (const [name, plan] of Object.entries(state.builds ?? {})) if (plan.design?.title) out[name] = plan.design.title
  for (const [name, fac] of Object.entries(state.facilities ?? {})) if (fac.title) out[name] = fac.title
  return out
}

// 呼び名をつける。知らない識別子と使えない呼び名は、理由を返して飛ばす。つけたら名前の一覧を返す
export function setTitles (state, titles) {
  const set = []
  const refused = {}
  for (const [name, raw] of Object.entries(titles ?? {})) {
    const title = String(raw ?? '').replace(/[\r\n\t]+/g, ' ').trim()
    const plan = state.builds?.[name]
    const fac = state.facilities?.[name]
    if (!plan && !fac) { refused[name] = 'there is no build or facility with that name'; continue }
    if (!title || title.length > MAX_TITLE) { refused[name] = `a title is 1-${MAX_TITLE} characters`; continue }
    if (plan) plan.design = { ...(plan.design ?? { name }), title }
    if (fac) fac.title = title
    set.push(name)
  }
  return { set, refused }
}

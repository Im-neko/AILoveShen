import { test } from 'node:test'
import assert from 'node:assert/strict'
import { titlesOf, setTitles, MAX_TITLE } from '../src/titles.mjs'
import { BuildPlan } from '../src/build.mjs'
import { buildsStatus } from '../src/builds.mjs'

const plan = (name) => new BuildPlan({ blocks: [{ x: 0, y: 0, z: 0, block: 'planks' }], width: 1, depth: 1, height: 1, design: { name, purpose: 'a forge', anchor: 'near_home' }, kind: 'build' })

test('建物と施設に呼び名をつけ、識別子は変えない。呼び名のないものは一覧に出ない', () => {
  const state = { builds: { workshop: plan('workshop'), annex: plan('annex') }, facilities: { wheat_farm: { name: 'wheat_farm', kind: 'farm' } } }
  assert.deepEqual(titlesOf(state), {})
  const r = setTitles(state, { workshop: 'ノクサス工房', wheat_farm: ' 黄昏の畑 ', ghost: 'x' })
  assert.deepEqual(r.set.sort(), ['wheat_farm', 'workshop'])
  assert.match(r.refused.ghost, /no build or facility/)
  assert.deepEqual(titlesOf(state), { workshop: 'ノクサス工房', wheat_farm: '黄昏の畑' })
  assert.equal(state.builds.workshop.design.name, 'workshop')
  assert.equal(state.builds.workshop.design.purpose, 'a forge')
})

test('呼び名は 1〜24 文字で、改行は空白にする。使えないものは理由を返して飛ばす', () => {
  const state = { builds: { workshop: plan('workshop') }, facilities: {} }
  assert.equal(setTitles(state, { workshop: '' }).refused.workshop.includes('1-24'), true)
  assert.equal(setTitles(state, { workshop: 'あ'.repeat(MAX_TITLE + 1) }).set.length, 0)
  setTitles(state, { workshop: '影の\n拳' })
  assert.equal(state.builds.workshop.design.title, '影の 拳')
})

test('呼び名は保存の形（toJSON）に残り、建物の一覧にも出る', () => {
  const state = { builds: { workshop: plan('workshop') }, facilities: {} }
  setTitles(state, { workshop: 'ノクサス工房' })
  assert.equal(state.builds.workshop.toJSON().design.title, 'ノクサス工房')
  const status = buildsStatus({ blockAt: () => ({ name: 'air' }) }, state)
  assert.equal(status[0].title, 'ノクサス工房')
})

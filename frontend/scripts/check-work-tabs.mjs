import assert from 'node:assert/strict'
import test from 'node:test'
import { closeWorkTab, openWorkTab, restoreWorkTabs, workTabFromHref, MAX_WORK_TABS } from '../lib/work-tabs.ts'

const href = '/studio/projects/p/operations?workspace=w&workflow=f&run=r&trace=t'
test('restoration retains source context but drops credentials and unknown destinations', () => {
  const tab = workTabFromHref(`${href}&token=secret&conversation=c&agent=1`, 'w')
  assert.ok(tab)
  const query = new URL(tab.href, 'https://test.invalid').searchParams
  assert.equal(query.get('conversation'), 'c')
  assert.equal(query.get('trace'), 't')
  assert.equal(query.get('project'), 'p')
  assert.equal(query.has('token'), false)
  for (const target of ['//evil.test', '/\\evil.test', 'javascript:alert(1)', '/providers?workspace=w', href.replace('workspace=w', 'workspace=other')]) {
    assert.equal(workTabFromHref(target, 'w'), null)
  }
  assert.deepEqual(restoreWorkTabs(JSON.stringify([{ href: tab.href, label: 'forged', id: 'forged' }, { href: '//evil.test' }]), 'w'), [tab])
  assert.deepEqual(restoreWorkTabs('{broken', 'w'), [])
  assert.deepEqual(restoreWorkTabs('null', 'w'), [])
})

test('open focuses an identity without duplicating or changing its position', () => {
  const first = workTabFromHref(href, 'w')
  const second = workTabFromHref(href.replace('run=r', 'run=r2'), 'w')
  const updated = workTabFromHref(href.replace('trace=t', 'trace=t2'), 'w')
  assert.equal(first.id, updated.id)
  assert.deepEqual(openWorkTab([first, second], updated), [updated, second])
  const entries = Array.from({ length: MAX_WORK_TABS + 2 }, (_, i) => workTabFromHref(href.replace('run=r', `run=r${i}`), 'w'))
  assert.equal(entries.reduce(openWorkTab, []).length, MAX_WORK_TABS)
})

test('close selects a neighbour only when closing the current work', () => {
  const tabs = [0, 1, 2].map((i) => workTabFromHref(href.replace('run=r', `run=r${i}`), 'w'))
  assert.equal(closeWorkTab(tabs, tabs[1].id, tabs[1].id).next.id, tabs[2].id)
  assert.equal(closeWorkTab(tabs, tabs[2].id, tabs[2].id).next.id, tabs[1].id)
  assert.equal(closeWorkTab(tabs, tabs[0].id, tabs[1].id).next, null)
  assert.deepEqual(closeWorkTab([tabs[0]], tabs[0].id, tabs[0].id), { tabs: [], next: null })
})

test('restored report tabs retain the artifact and data view without mixing report identities', () => {
  const report = '/studio/projects/p/data?workspace=w&workflow=f&run=r&artifact=s%3Aa&view=artifacts'
  const first = workTabFromHref(report, 'w')
  const second = workTabFromHref(report.replace('s%3Aa', 's%3Ab'), 'w')
  assert.notEqual(first.id, second.id)
  const restored = restoreWorkTabs(JSON.stringify([first, second]), 'w')
  assert.equal(restored.length, 2)
  const query = new URL(restored[0].href, 'https://test.invalid').searchParams
  assert.equal(query.get('artifact'), 's:a')
  assert.equal(query.get('view'), 'artifacts')
  assert.equal(query.get('run'), 'r')
  assert.equal(workTabFromHref(report.replace('view=artifacts', 'view=unknown'), 'w').href.includes('view='), false)
  assert.deepEqual(restoreWorkTabs(JSON.stringify(restored), 'other'), [])
})

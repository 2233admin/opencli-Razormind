import assert from 'node:assert/strict'
import { test } from 'node:test'
import { navigationBreadcrumbs } from '../lib/navigation-breadcrumbs.ts'

const labels = { '/studio': '项目', '/providers': '模型与连接', '/control': '控制', '/control/actions': '控制记录' }
const crumbs = (path, query = '') => navigationBreadcrumbs(path, new URLSearchParams(query), labels)

test('a project detail breadcrumb identifies its actual view and provides scoped parent links', () => {
  const result = crumbs('/studio/projects/project-1/operations', 'workspace=space+one&workflow=flow-1&run=run-1&trace=trace-1')
  assert.equal(result.at(-1).label, '运行记录')
  assert.equal(result.at(-1).href, undefined)
  assert.equal(result[1].href, '/studio?workspace=space+one')
  assert.equal(result[2].href, '/studio/projects/project-1?workspace=space+one')
  for (const item of result.filter((item) => item.href)) {
    const url = new URL(item.href, 'http://app.test')
    assert.equal(url.searchParams.has('run'), false)
    assert.equal(url.searchParams.has('trace'), false)
  }
})

test('editor navigation returns to the query-selected project and encodes its identifier', () => {
  const result = crumbs('/studio/workflow', 'workspace=space-1&project=project%2Fone')
  assert.equal(result.at(-1).label, '业务编排')
  assert.equal(result[2].href, '/studio/projects/project%2Fone?workspace=space-1')
})

test('missing workspace does not invent a valid project destination', () => {
  const result = crumbs('/studio/projects/project-1/data')
  assert.equal(result.at(-1).label, '数据工作台')
  assert.deepEqual(result.filter((item) => item.href).map((item) => item.href), ['/dashboard', '/studio'])
})

test('overview has one current crumb and nested global routes choose the nearest parent', () => {
  assert.deepEqual(crumbs('/dashboard'), [{ label: '概览' }])
  assert.equal(crumbs('/control/actions/action-1').at(-2).href, '/control/actions')
  assert.equal(crumbs('/providers').at(-1).label, '模型与连接')
})

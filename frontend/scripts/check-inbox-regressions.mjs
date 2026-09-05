import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('inbox combines existing operational signals with server-backed human approvals', async () => {
  const page = await read('app/(app)/inbox/page.tsx')
  const approvalDetail = await read('components/inbox/queue-detail.tsx')
  const conversationThread = await read('components/inbox/inbox-conversation-thread.tsx')
  const hooks = await read('lib/api/hooks.ts')
  const endpoints = await read('lib/api/endpoints.ts')

  assert.match(page, /useInfiniteTasks\(\{ status: 'failed', limit: 100 \}, \{ enabled: pendingActive \}\)/)
  assert.match(page, /useInfiniteTasks\(\{ status: 'pending', limit: 100 \}, \{ enabled: pendingActive \}\)/)
  assert.match(page, /useInfiniteNotificationLogs\(\{ limit: 100 \}, \{ enabled: pendingActive \}\)/)
  assert.match(page, /useInfiniteControlActions\(\s*\{ outcome: 'pending', limit: 100 \},\s*\{ enabled: pendingActive \},?\s*\)/)
  assert.match(hooks, /export function useInfiniteTasks/)
  assert.match(hooks, /export function useInfiniteNotificationLogs/)
  assert.match(hooks, /export function useInfiniteControlActions/)
  assert.match(
    endpoints,
    /listNotificationLogs = \(params\?: \{\s*rule_id\?: string;\s*page\?: number;\s*limit\?: number;\s*\}\) =>/,
  )
  assert.match(page, /useOperationsInbox\(workspaceId, 'open'/)
  assert.match(approvalDetail, /ApprovalQueueDetail/)
  assert.match(page, /type: 'change_proposal', status: 'open'/)
  assert.match(page, /proposalToQueueItem/)
  assert.match(approvalDetail, /ProposalQueueDetail/)
  assert.match(approvalDetail, /请在原 Agent 会话中确认或调整此提案/)
  assert.doesNotMatch(approvalDetail, /decideOperationsApproval[\s\S]*ProposalQueueDetail/)
  assert.match(approvalDetail, /InboxConversationThread/)
  assert.match(conversationThread, /send\.mutateAsync/)
  assert.match(conversationThread, /context,/)
  assert.match(conversationThread, /InboxConversationUnavailable/)
})

test('inbox uses a Linear-style queue while preserving destinations for underlying records', async () => {
  const page = await read('app/(app)/inbox/page.tsx')
  const detail = await read('components/inbox/queue-detail.tsx')

  assert.match(page, /data-testid="inbox-workbench"/)
  assert.match(page, /lg:h-\[calc\(100dvh-3\.5rem\)\]/)
  assert.match(page, /data-testid="inbox-queue-scroll"/)
  assert.match(detail, /data-testid="inbox-detail-scroll"/)
  assert.doesNotMatch(page, /<PageContainer/)
  assert.doesNotMatch(page, /className="overflow-hidden rounded-xl border bg-card shadow-sm"/)
  assert.match(page, /ACTION_CENTER_TABS/)
  assert.match(page, /groupQueueItems/)
  assert.match(page, /role="listbox"/)
  assert.match(page, /aria-label="所选信号详情"/)
  assert.match(page, /搜索当前队列/)
  assert.match(page, /按严重程度排列，重复信号自动合并/)
  assert.match(page, /router\.push\(selectedItem\.href\)/)
  assert.match(page, /event\.key\.toLowerCase\(\) === 'j'/)
  assert.match(page, /event\.key\.toLowerCase\(\) === 'k'/)
  assert.match(page, /scrollIntoView\(\{ block: 'nearest' \}\)/)
  assert.match(page, /\[content-visibility:auto\]/)
  assert.match(page, /href: `\/tasks\/\$\{task\.id\}`/)
  assert.match(page, /href: '\/inbox\?tab=notifications'/)
  assert.match(page, /href: '\/inbox\?tab=controls'/)
  assert.match(detail, /href=\{`\/sources\/\$\{item\.sourceId\}`\}/)
  assert.match(page, /项目动态/)
})

test('inbox preserves queue state and progressively loads hundreds-scale signal sets', async () => {
  const page = await read('app/(app)/inbox/page.tsx')

  assert.match(page, /useSearchParams\(\)/)
  assert.match(page, /searchParams\.get\('view'\)/)
  assert.match(page, /searchParams\.get\('q'\)/)
  assert.match(page, /const searchParamsKey = searchParams\.toString\(\)/)
  assert.doesNotMatch(page, /\}, \[searchParams\]\)/)
  assert.match(page, /router\.replace\(/)
  assert.match(page, /\.pages\.flatMap\(\(page\) => page\.data\)/)
  assert.match(page, /hasMoreSignals/)
  assert.match(page, /isFetchingNextPage/)
  assert.match(page, /加载更多信号/)
  assert.match(page, /已加载/)
})

test('inbox renders explicit initial, partial, empty, and total failure states', async () => {
  const page = await read('app/(app)/inbox/page.tsx')

  assert.match(page, /const isInitialLoading =\s+queries\.every/)
  assert.match(page, /const isTotalFailure =\s+queries\.every/)
  assert.match(page, /const partialFailures =/)
  assert.match(page, /暂时无法读取，其余信号仍可处理/)
  assert.match(page, /当前视图已经清空/)
  assert.match(page, /<LoadingState rows=\{5\}/)
  assert.match(page, /<ErrorState/)
  assert.match(page, /重新读取/)
})

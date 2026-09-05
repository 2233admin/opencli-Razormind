import { buildRunUrl, type RunNavigationContext } from '../studio/run-navigation'

export type InboxOrigin = RunNavigationContext & {
  conversation?: string
  unavailableReason?: 'missing' | 'invalid' | 'scope-mismatch'
}

export type InboxOriginAction = { label: string; href: string }

const REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/

function reference(value: unknown) {
  return typeof value === 'string' && REFERENCE.test(value.trim()) ? value.trim() : undefined
}

/**
 * Treat API evidence as data, never as a destination. Only the small set of
 * server-projected identifiers below can participate in navigation, and the
 * work item's workspace remains the authority for the scope boundary.
 */
export function readInboxOrigin(
  evidence: Record<string, unknown>,
  workspace: string,
): InboxOrigin {
  const projectedWorkspace = reference(evidence.workspace_id)
  const hasWorkspace = evidence.workspace_id !== undefined && evidence.workspace_id !== null && evidence.workspace_id !== ''
  if (hasWorkspace && !projectedWorkspace) return { unavailableReason: 'invalid' }
  if (projectedWorkspace && projectedWorkspace !== workspace) return { unavailableReason: 'scope-mismatch' }

  const project = reference(evidence.project_id)
  const workflow = reference(evidence.workflow_id)
  const run = reference(evidence.run_id)
  const conversation = reference(evidence.conversation_id)
  const hasKnownKey = ['workspace_id', 'project_id', 'workflow_id', 'run_id', 'conversation_id']
    .some((key) => evidence[key] !== undefined && evidence[key] !== null && evidence[key] !== '')

  if (!project && !workflow && !run && !conversation) {
    return { unavailableReason: hasKnownKey ? 'invalid' : 'missing' }
  }
  return { workspace, project, workflow, run, conversation }
}

export function inboxOriginActions(origin: InboxOrigin): InboxOriginAction[] {
  if (origin.unavailableReason || !origin.workspace) return []
  const context: RunNavigationContext = {
    workspace: origin.workspace,
    project: origin.project,
    workflow: origin.workflow,
    run: origin.run,
  }
  const actions: InboxOriginAction[] = []
  if (origin.project) {
    actions.push({ label: '查看项目', href: `/studio/projects/${encodeURIComponent(origin.project)}?workspace=${encodeURIComponent(origin.workspace)}` })
    const hasExactRun = Boolean(origin.workflow && origin.run)
    if (!hasExactRun) {
      const href = buildRunUrl('operations', context)
      if (href) actions.push({ label: '运行记录', href })
    }
    for (const [label, section] of [[hasExactRun ? '查看运行' : null, 'operations'], ['查看数据', 'data'], ['查看证据', 'evidence']] as const) {
      if (!label) continue
      const href = buildRunUrl(section, context)
      if (href) actions.push({ label, href })
    }
  }
  if (origin.conversation) {
    const params = new URLSearchParams({ agent: '1', conversation: origin.conversation, workspace: origin.workspace })
    if (origin.project) params.set('project', origin.project)
    if (origin.workflow) params.set('workflow', origin.workflow)
    if (origin.run) params.set('run', origin.run)
    actions.push({ label: '继续原会话', href: `/inbox?${params.toString()}` })
  }
  return actions
}

export function inboxOriginUnavailableMessage(origin: InboxOrigin) {
  if (origin.unavailableReason === 'scope-mismatch') return '来源上下文不属于当前工作区，无法打开。'
  if (origin.unavailableReason === 'invalid') return '来源上下文字段无效，无法打开。'
  return '此信号没有可验证的项目、运行或原会话来源。'
}

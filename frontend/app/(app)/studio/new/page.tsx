'use client'

import {
  AlertTriangle,
  Bot,
  Check,
  ChevronRight,
  Clock3,
  CornerDownLeft,
  Database,
  FileDiff,
  LoaderCircle,
  Mail,
  Play,
  Rocket,
  Save,
  Sparkles,
  X,
} from 'lucide-react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { PageContainer } from '@/components/shell/page-container'
import { ContextPageLayout } from '@/components/experience/context-page-layout'
import { useProjectCreation } from '@/components/studio/project-create-form'
import { AgentBuilderCanvas } from '@/components/studio/agent-builder-canvas'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useMyWorkspaces } from '@/lib/api/hooks'
import { updateProjectWorkflowDraft } from '@/lib/api/endpoints'
import { analyzeGeneratedWorkflowReadiness, extractWorkflowSchedule, extractWorkflowSource, generateWorkflowLocally } from '@/lib/flow/local-generate'
import type { GeneratedWorkflowSpec } from '@/lib/flow/types'
import { generatedSpecToWorkflowProject } from '@/lib/workflow/generated-project'
import { studioGraphForTemplate } from '@/lib/workflow/studio-templates'

const STARTERS = [
  '每天汇总 AI 行业新闻，提取重点后发到我的邮箱',
  '监控竞品官网和社交平台，发现重要变化后生成简报',
  '每天早上整理关注主题的新内容，只发送首次出现的信息',
]

type Message = { role: 'agent' | 'user'; content: string }
type RequirementState = {
  emailReady: boolean
  emailRequired: boolean
  entryMode: 'manual' | 'scheduled'
  finalEmail: string
  inferredEmail: string
  scheduleReady: boolean
  sourceReady: boolean
}
type CapabilityGap = { detail: string; id: string; label: string; nodeId?: string }
type PendingPatch = {
  conflicts: string[]
  spec: GeneratedWorkflowSpec
  summary: string[]
}
type DurableDraft = { graphId: string; projectId: string; revision: number; workflowId: string; workspaceId: string }

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const EMAIL_CAPTURE_PATTERN = /[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/
function inspectRequirements(requirementText: string, deliveryEmail: string): RequirementState {
  const inferredEmail = requirementText.match(EMAIL_CAPTURE_PATTERN)?.[0] ?? ''
  const finalEmail = deliveryEmail.trim() || inferredEmail
  const scheduleReady = extractWorkflowSchedule(requirementText) !== null
  const emailRequired = /(邮件|邮箱|email|mail|发给|发送给)/i.test(requirementText)

  return {
    emailReady: EMAIL_PATTERN.test(finalEmail),
    emailRequired,
    entryMode: scheduleReady ? 'scheduled' : 'manual',
    finalEmail,
    inferredEmail,
    scheduleReady,
    sourceReady: extractWorkflowSource(requirementText) !== null,
  }
}

function nextRequirementPrompt(state: RequirementState) {
  if (!state.sourceReady) return '先补充数据来源：要查看哪些网站、平台、RSS 或具体链接？'
  if (state.emailRequired && !state.emailReady) return '已识别邮件输出。请填写接收邮箱；Draft 可以先保存，但发布和运行会保持阻止。'
  if (!state.scheduleReady) return '当前按一次性查询创建 Manual Trigger；如果要持续监控，再补充频率即可。'
  return '当前要求已形成可编辑 Draft，你可以在画布手工调整，或继续对话生成 Patch。'
}

function capabilityGaps(spec: GeneratedWorkflowSpec | null, state: RequirementState): CapabilityGap[] {
  const gaps: CapabilityGap[] = (spec?.capabilityGaps ?? [])
    .filter((gap) => {
      if (!state.emailReady) return true
      const node = spec?.nodes.find((candidate) => candidate.id === gap.nodeId)
      return node?.type !== 'email-output' && !/(email|邮件|收件)/i.test(`${gap.title} ${gap.detail}`)
    })
    .map((gap) => ({ id: gap.id, nodeId: gap.nodeId, label: gap.title, detail: gap.detail }))
  const hasSourceGap = gaps.some((gap) => /(source|来源|endpoint|api)/i.test(`${gap.label} ${gap.detail}`))
  const hasEmailGap = gaps.some((gap) => /(email|邮件|收件)/i.test(`${gap.label} ${gap.detail}`))
  if (!state.sourceReady && !hasSourceGap) {
    gaps.push({ id: 'source', label: '数据来源待配置', detail: '补充网站、API、RSS 或具体链接；不完整 Draft 仍可保存。' })
  }
  if (state.emailRequired && !state.emailReady && !hasEmailGap) {
    gaps.push({ id: 'email-target', label: '邮件目标待配置', detail: '填写有效邮箱并在发布前绑定真实邮件连接。' })
  }
  for (const node of spec?.nodes ?? []) {
    const searchable = `${node.type} ${node.label} ${node.description}`.toLowerCase()
    const config = node.config?.trim() ?? ''
    if (/(webhook)/.test(searchable) && !/https?:\/\//i.test(config)) {
      gaps.push({ id: `webhook-${node.id}`, nodeId: node.id, label: `${node.label} 缺少 Endpoint`, detail: 'Webhook 输出需要 HTTPS Endpoint。' })
    }
    if (/(api agent|api-agent|http)/.test(searchable) && !/https?:\/\//i.test(config)) {
      gaps.push({ id: `api-${node.id}`, nodeId: node.id, label: `${node.label} 缺少 API 配置`, detail: 'API Agent 需要 URL 和连接凭据引用。' })
    }
    if (/(governed tool|tool agent|tool-agent)/.test(searchable) && !config) {
      gaps.push({ id: `tool-${node.id}`, nodeId: node.id, label: `${node.label} 缺少 Tool 绑定`, detail: '选择受治理 Tool 与版本后才能运行。' })
    }
  }
  return gaps
}

function describePatch(current: GeneratedWorkflowSpec, next: GeneratedWorkflowSpec): string[] {
  const currentNodes = new Map(current.nodes.map((node) => [node.id, node]))
  const nextNodes = new Map(next.nodes.map((node) => [node.id, node]))
  const added = next.nodes.filter((node) => !currentNodes.has(node.id)).length
  const removed = current.nodes.filter((node) => !nextNodes.has(node.id)).length
  const changed = next.nodes.filter((node) => {
    const previous = currentNodes.get(node.id)
    return previous && JSON.stringify(previous) !== JSON.stringify(node)
  }).length
  const edgeDelta = next.edges.length - current.edges.length
  return [
    added ? `新增 ${added} 个节点` : '',
    removed ? `移除 ${removed} 个节点` : '',
    changed ? `更新 ${changed} 个节点` : '',
    edgeDelta ? `${edgeDelta > 0 ? '新增' : '移除'} ${Math.abs(edgeDelta)} 条连接` : '',
  ].filter(Boolean)
}

function mergePatchPreservingManual(current: GeneratedWorkflowSpec, next: GeneratedWorkflowSpec): GeneratedWorkflowSpec {
  const currentNodes = new Map(current.nodes.map((node) => [node.id, node]))
  const nextNodes = new Map(next.nodes.map((node) => [node.id, node]))
  const currentEdges = new Map(current.edges.map((edge) => [`${edge.source}->${edge.target}`, edge]))
  return {
    ...next,
    nodes: next.nodes.map((node) => {
      const manual = currentNodes.get(node.id)
      return manual?.type === node.type
        ? { ...node, label: manual.label, config: manual.config, params: { ...(node.params ?? {}), ...(manual.params ?? {}) } }
        : node
    }),
    edges: next.edges.map((edge) => {
      const manual = currentEdges.get(`${edge.source}->${edge.target}`)
      const sameLogicalEndpoints = currentNodes.get(edge.source)?.type === nextNodes.get(edge.source)?.type
        && currentNodes.get(edge.target)?.type === nextNodes.get(edge.target)?.type
      return manual && sameLogicalEndpoints
        ? { ...edge, label: manual.label ?? edge.label, mapping: manual.mapping ?? edge.mapping }
        : edge
    }),
  }
}

export default function NewAgentStudioPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const workspaces = useMyWorkspaces()
  const projectCreation = useProjectCreation()
  const [workspaceId, setWorkspaceId] = useState<string | null>(searchParams.get('workspace'))
  const [messages, setMessages] = useState<Message[]>([
    { role: 'agent', content: '先告诉我：这个项目要持续完成什么工作？可以写清数据从哪里来、需要怎样判断、最后交付到哪里。' },
  ])
  const [input, setInput] = useState('')
  const [spec, setSpec] = useState<GeneratedWorkflowSpec | null>(null)
  const [name, setName] = useState('')
  const [deliveryEmail, setDeliveryEmail] = useState('')
  const [generating, setGenerating] = useState(false)
  const [manualEditVersion, setManualEditVersion] = useState(0)
  const [manualEditSummaries, setManualEditSummaries] = useState<string[]>([])
  const [pendingPatch, setPendingPatch] = useState<PendingPatch | null>(null)
  const [durableDraft, setDurableDraft] = useState<DurableDraft | null>(null)
  const [durableSaving, setDurableSaving] = useState(false)
  const durableDraftRef = useRef<DurableDraft | null>(null)
  const draftSaveChainRef = useRef<Promise<void>>(Promise.resolve())
  const lastPersistedGraphRef = useRef('')
  const autosaveTimerRef = useRef<number | null>(null)

  useEffect(() => {
    if (!workspaces.data?.length) return
    if (workspaceId && workspaces.data.some((workspace) => workspace.id === workspaceId)) return
    const preferredWorkspaceId = window.localStorage.getItem('opencli:studio-workspace')
    const preferredWorkspace = workspaces.data.find((workspace) => workspace.id === preferredWorkspaceId)
    setWorkspaceId(preferredWorkspace?.id ?? (workspaces.data.length === 1 ? workspaces.data[0].id : null))
  }, [workspaceId, workspaces.data])

  useEffect(() => {
    if (workspaceId && workspaces.data?.some((workspace) => workspace.id === workspaceId)) {
      window.localStorage.setItem('opencli:studio-workspace', workspaceId)
    }
  }, [workspaceId, workspaces.data])

  useEffect(() => () => {
    if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current)
  }, [])

  const userRequirements = useMemo(
    () => messages.filter((message) => message.role === 'user').map((message) => message.content),
    [messages],
  )
  const requirementText = userRequirements.join('；')
  const requirementState = inspectRequirements(requirementText, deliveryEmail)
  const { emailReady, emailRequired, entryMode, inferredEmail, sourceReady } = requirementState
  const gaps = capabilityGaps(spec, requirementState)
  const analyzedReadiness = spec ? analyzeGeneratedWorkflowReadiness(spec) : null
  const workflowReadiness = analyzedReadiness
    ? {
        ...analyzedReadiness,
        status: gaps.length ? 'incomplete' as const : 'ready' as const,
        canPublish: gaps.length === 0,
        canRun: gaps.length === 0,
        blockingGapIds: gaps.map((gap) => gap.id),
      }
    : null
  const gapNodeIds = gaps.flatMap((gap) => (gap.nodeId ? [gap.nodeId] : []))
  const readinessItems = [
    { ready: userRequirements.length > 0, label: entryMode === 'scheduled' ? '定时入口' : '一次性入口', Icon: Clock3 },
    { ready: sourceReady, label: '数据来源', Icon: Database },
    { ready: emailRequired ? emailReady : true, label: emailRequired ? '邮件输出' : 'Records 输出', Icon: emailRequired ? Mail : Save },
  ]
  const readiness = readinessItems.filter((item) => item.ready).length
  const emailInputInvalid = deliveryEmail.trim().length > 0 && !EMAIL_PATTERN.test(deliveryEmail.trim())
  const studioHref = workspaceId ? `/studio?workspace=${workspaceId}` : '/studio'

  function graphForDraft(nextSpec: GeneratedWorkflowSpec, draftName: string, state: RequirementState) {
    const graph = generatedSpecToWorkflowProject(
      nextSpec,
      draftName,
      state.emailRequired && state.emailReady ? { deliveryEmail: state.finalEmail } : {},
    )
    return durableDraftRef.current ? { ...graph, id: durableDraftRef.current.graphId } : graph
  }

  function setDurableDraftState(next: DurableDraft) {
    durableDraftRef.current = next
    setDurableDraft(next)
  }

  async function updateDurableDraft(nextSpec: GeneratedWorkflowSpec, draftName: string, state: RequirementState) {
    const graph = graphForDraft(nextSpec, draftName, state)
    const fingerprint = JSON.stringify(graph)
    if (fingerprint === lastPersistedGraphRef.current) return
    const save = draftSaveChainRef.current.then(async () => {
      const current = durableDraftRef.current
      if (!current || !workspaceId || current.workspaceId !== workspaceId) return
      setDurableSaving(true)
      const updated = await updateProjectWorkflowDraft(current.workspaceId, current.projectId, current.workflowId, graph, current.revision)
      setDurableDraftState({ ...current, graphId: updated.graph.id, revision: updated.revision })
      lastPersistedGraphRef.current = JSON.stringify(updated.graph)
    })
    draftSaveChainRef.current = save.catch(() => undefined)
    try {
      await save
    } finally {
      setDurableSaving(false)
    }
  }

  async function ensureDurableDraft(
    nextSpec: GeneratedWorkflowSpec,
    draftName: string,
    requirements: string[],
    state: RequirementState,
  ) {
    if (!workspaceId) return
    if (durableDraftRef.current) {
      await updateDurableDraft(nextSpec, draftName, state)
      return
    }
    const graph = graphForDraft(nextSpec, draftName, state)
    const draftWorkspaceId = workspaceId
    setDurableSaving(true)
    try {
      const result = await projectCreation.create({ workspaceId: draftWorkspaceId, name: draftName, description: requirements.join('；') || 'Agent Builder Draft', graph, appType: 'agent' })
      setDurableDraftState({
        graphId: result.draft.graph.id,
        projectId: result.project.id,
        revision: result.draft.revision,
        workflowId: result.primary_workflow.id,
        workspaceId: draftWorkspaceId,
      })
      lastPersistedGraphRef.current = JSON.stringify(result.draft.graph)
      toast.success('第一个有效意图已创建 durable Project Draft')
    } finally {
      setDurableSaving(false)
    }
  }

  function queueDurableDraftSave(nextSpec: GeneratedWorkflowSpec, draftName: string, state: RequirementState) {
    if (!durableDraftRef.current) return
    if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current)
    autosaveTimerRef.current = window.setTimeout(() => {
      autosaveTimerRef.current = null
      void updateDurableDraft(nextSpec, draftName, state).catch((reason) => {
        toast.error(reason instanceof Error ? `Draft 自动保存失败：${reason.message}` : 'Draft 自动保存失败')
      })
    }, 700)
  }

  async function generateDraft(requirements = userRequirements) {
    if (!requirements.length || generating) return

    const isRevision = spec !== null
    setGenerating(true)
    try {
      let generated: GeneratedWorkflowSpec
      let localFallback = false
      try {
        const response = await fetch('/api/generate-workflow', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: requirements.join('\n补充要求：') }),
        })
        const payload = await response.json()
        if (!response.ok) throw new Error(payload?.message ?? 'Agent generation failed')
        generated = payload as GeneratedWorkflowSpec
      } catch {
        generated = generateWorkflowLocally(requirements.join('；'))
        localFallback = true
      }

      const nextState = inspectRequirements(requirements.join('；'), deliveryEmail)
      const nextGaps = capabilityGaps(generated, nextState)
      if (isRevision && spec && manualEditVersion > 0) {
        const summary = describePatch(spec, generated)
        setPendingPatch({
          spec: generated,
          summary: summary.length ? summary : ['更新 Agent 生成配置'],
          conflicts: manualEditSummaries.length ? manualEditSummaries : ['画布包含尚未合并的手工编辑'],
        })
      } else {
        setSpec(generated)
      }
      const draftName = isRevision && name.trim() ? name.trim() : generated.title
      setName(draftName)
      setMessages((current) => [
        ...current,
        {
          role: 'agent',
          content: isRevision && manualEditVersion > 0
            ? '我已生成 Patch / Diff。检测到手工画布编辑冲突，请先确认如何合并。'
            : `${localFallback ? 'AI 服务暂时不可用，已用本地规则生成。' : ''}当前 Draft 有 ${generated.nodes.length} 个节点、${generated.edges.length} 条连接。${nextGaps.length ? `发现 ${nextGaps.length} 项 Capability Gap；可以保存，但发布与运行已阻止。` : nextRequirementPrompt(nextState)}`,
        },
      ])
      if (!(isRevision && spec && manualEditVersion > 0)) {
        try {
          await ensureDurableDraft(generated, draftName, requirements, nextState)
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : 'Project Draft 持久化失败'
          toast.error(message)
          setMessages((current) => [...current, { role: 'agent', content: `画布已生成，但 durable Draft 自动保存失败：${message}` }])
        }
      }
    } finally {
      setGenerating(false)
    }
  }

  async function askAgent(text = input) {
    const requirement = text.trim()
    if (!requirement || generating) return

    const nextRequirements = [...userRequirements, requirement]
    setMessages((current) => [...current, { role: 'user', content: requirement }])
    setInput('')
    await generateDraft(nextRequirements)
  }

  async function persistProject(useBlank = false) {
    if (!workspaceId || (!useBlank && (!spec || !name.trim()))) return

    const finalName = name.trim() || '未命名项目'
    try {
      if (spec && !useBlank) {
        await ensureDurableDraft(spec, finalName, userRequirements, requirementState)
        const current = durableDraftRef.current
        if (!current) throw new Error('durable Project Draft 尚未创建')
        toast.success(gaps.length
          ? `项目草稿已保存；${gaps.length} 项 Capability Gap 会阻止发布和运行`
          : '项目草稿已保存；验证后请确认真实投递连接再发布')
        router.push(`/studio/workflow?workspace=${current.workspaceId}&project=${current.projectId}&workflow=${current.workflowId}`)
        return
      }

      const graph = studioGraphForTemplate('blank', finalName)
      const result = await projectCreation.create({ workspaceId, name: finalName, description: userRequirements.join('；') || '从空白画布创建', graph, appType: 'workflow' })
      toast.success('空白项目已创建')
      router.push(`/studio/workflow?workspace=${workspaceId}&project=${result.project.id}&workflow=${result.primary_workflow.id}&guide=blank`)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '创建失败')
    }
  }

  function recordManualEdit(summary: string) {
    setManualEditVersion((current) => current + 1)
    setManualEditSummaries((current) => [...new Set([...current, summary])].slice(-5))
  }

  function handleCanvasSpecChange(nextSpec: GeneratedWorkflowSpec) {
    setSpec(nextSpec)
    queueDurableDraftSave(nextSpec, name.trim() || nextSpec.title, requirementState)
  }

  function handleNameChange(nextName: string) {
    setName(nextName)
    if (spec) queueDurableDraftSave(spec, nextName.trim() || spec.title, requirementState)
  }

  function handleDeliveryEmailChange(nextEmail: string) {
    setDeliveryEmail(nextEmail)
    if (spec) queueDurableDraftSave(spec, name.trim() || spec.title, inspectRequirements(requirementText, nextEmail))
  }

  async function confirmPatch(preserveManual: boolean) {
    if (!pendingPatch || !spec) return
    const nextSpec = preserveManual ? mergePatchPreservingManual(spec, pendingPatch.spec) : pendingPatch.spec
    setSpec(nextSpec)
    setPendingPatch(null)
    setManualEditVersion(0)
    setManualEditSummaries([])
    setMessages((current) => [
      ...current,
      { role: 'agent', content: preserveManual ? 'Patch 已合并，冲突字段保留手工画布版本。' : 'Patch 已确认应用，画布已更新为 Agent 版本。' },
    ])
    try {
      await ensureDurableDraft(nextSpec, name.trim() || nextSpec.title, userRequirements, requirementState)
    } catch (reason) {
      toast.error(reason instanceof Error ? `Patch 保存失败：${reason.message}` : 'Patch 保存失败')
    }
  }

  return (
    <PageContainer
      title="与 Agent 创建 Workflow Draft"
      eyebrow="Studio · Agent builder"
      description="一次性查询与定时监控都从节点开始。Draft 可以不完整保存；Capability Gap 会明确阻止发布和运行。"
      className="max-w-none"
      actions={(
        <Button size="icon" variant="ghost" className="min-h-11 min-w-11" nativeButton={false} render={<Link href={studioHref} />} aria-label="关闭">
          <X className="size-4" />
        </Button>
      )}
    >
      {(workspaces.data?.length ?? 0) > 1 ? (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border bg-muted/20 p-2.5 text-xs">
          <span className="font-medium">保存到 Workspace</span>
          <Select value={workspaceId ?? ''} onValueChange={(value) => setWorkspaceId(value || null)} disabled={Boolean(durableDraft) || durableSaving || projectCreation.isPending}>
            <SelectTrigger className="min-h-11 min-w-52 bg-background" aria-label="选择项目 Workspace"><SelectValue>{(workspaces.data ?? []).find((workspace) => workspace.id === workspaceId)?.name ?? '选择已授权 Workspace'}</SelectValue></SelectTrigger>
            <SelectContent>{(workspaces.data ?? []).map((workspace) => <SelectItem key={workspace.id} value={workspace.id}>{workspace.name}</SelectItem>)}</SelectContent>
          </Select>
          {!workspaceId ? <span className="text-muted-foreground">未选择时不会创建草稿。</span> : null}
          {durableDraft ? <span className="text-muted-foreground">草稿属于 {((workspaces.data ?? []).find((workspace) => workspace.id === durableDraft.workspaceId)?.name ?? durableDraft.workspaceId)}。打开草稿后可创建另一个项目。</span> : null}
        </div>
      ) : null}
      <ContextPageLayout context={<>
        <section className="flex min-h-0 min-w-0 flex-col" aria-label="Agent 对话 Dock">
          <div className="border-b p-4 sm:p-5">
            <div className="flex items-center gap-3">
              <div className="grid size-10 shrink-0 place-items-center rounded-md bg-foreground text-background">
                <Bot className="size-5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <h2 className="text-sm font-semibold">OpenCLI 项目 Agent</h2>
                  <span className="font-mono text-3xs text-muted-foreground">{readiness}/3 已明确</span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 font-mono text-3xs text-muted-foreground">
                  <span className="size-1.5 rounded-full bg-foreground/60" />
                  {generating
                    ? '正在生成项目方案'
                    : durableSaving
                      ? '正在自动保存同一 Project Draft'
                      : durableDraft
                        ? `Project Draft r${durableDraft.revision} 已保存`
                        : spec
                          ? '方案可继续调整'
                          : '正在整理项目要求'}
                </div>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              {readinessItems.map(({ ready, label, Icon }) => (
                <div
                  key={label}
                  aria-label={`${label}：${ready ? '已明确' : '待补充'}`}
                  className={`flex min-w-28 flex-1 items-center gap-2 rounded-sm border px-2.5 py-2 text-3xs ${ready ? 'border-foreground/20 bg-muted/70 text-foreground' : 'bg-background/40 text-muted-foreground'}`}
                >
                  <Icon className="size-3 shrink-0" />
                  <span className="whitespace-nowrap">{label}</span>
                  {ready ? <Check className="ml-auto size-3 shrink-0 text-foreground" /> : null}
                </div>
              ))}
            </div>
          </div>

          <div
            className="min-h-48 max-h-96 flex-1 space-y-4 overflow-y-auto p-4 sm:p-5 xl:max-h-none"
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
            aria-busy={generating}
          >
            {messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`flex min-w-0 gap-3 ${message.role === 'user' ? 'justify-end' : ''}`}>
                {message.role === 'agent' ? (
                  <div className="mt-1 grid size-7 shrink-0 place-items-center rounded-sm border bg-background">
                    <Sparkles className="size-3.5" />
                  </div>
                ) : null}
                <div className={`max-w-[85%] min-w-0 break-words rounded-md px-3.5 py-3 text-xs leading-5 [overflow-wrap:anywhere] ${message.role === 'user' ? 'bg-foreground text-background' : 'border bg-background/70 text-muted-foreground'}`}>
                  {message.content}
                </div>
              </div>
            ))}
            {pendingPatch ? (
              <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3" role="alert">
                <div className="flex items-center gap-2 text-xs font-semibold"><FileDiff className="size-3.5" />Patch / Diff</div>
                <p className="mt-2 text-3xs leading-4 text-muted-foreground">检测到手工画布编辑冲突，应用前需要确认。</p>
                <ul className="mt-2 space-y-1 text-3xs text-muted-foreground">
                  {pendingPatch.summary.map((item) => <li key={item}>+ {item}</li>)}
                  {pendingPatch.conflicts.map((item) => <li key={item} className="text-amber-700 dark:text-amber-300">! {item}</li>)}
                </ul>
                <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
                  <Button size="sm" variant="outline" onClick={() => void confirmPatch(true)}>保留手工编辑</Button>
                  <Button size="sm" onClick={() => void confirmPatch(false)}>确认应用 Patch</Button>
                </div>
              </div>
            ) : null}
            {generating ? (
              <div className="flex items-center gap-3 text-xs text-muted-foreground" role="status">
                <LoaderCircle className="size-4 animate-spin" />
                正在拆解目标、选择节点并检查连接…
              </div>
            ) : null}
          </div>

          {!userRequirements.length ? (
            <div className="px-4 pb-3 sm:px-5">
              <div className="mb-2 font-mono text-3xs text-muted-foreground">从一个例子开始</div>
              <div className="space-y-1">
                {STARTERS.map((starter) => (
                  <button
                    key={starter}
                    type="button"
                    onClick={() => void askAgent(starter)}
                    className="flex min-h-11 w-full items-start justify-between gap-3 rounded-xs border bg-background/50 px-3 py-2 text-left text-2xs text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground"
                  >
                    <span className="min-w-0 break-words">{starter}</span>
                    <ChevronRight className="mt-0.5 size-3 shrink-0" />
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          <div className="border-t bg-background/70 p-3 sm:p-4">
            {emailRequired ? <div className="mb-3 space-y-1.5 text-xs">
              <label htmlFor="delivery-email" className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 font-medium">
                <span>简报收件邮箱</span>
                <span className="font-normal text-muted-foreground">P0 交付方式</span>
              </label>
              <Input
                id="delivery-email"
                type="email"
                inputMode="email"
                autoComplete="email"
                value={deliveryEmail}
                onChange={(event) => handleDeliveryEmailChange(event.target.value)}
                placeholder={inferredEmail || 'name@example.com'}
                aria-invalid={emailInputInvalid || undefined}
                aria-describedby={emailInputInvalid ? 'delivery-email-help delivery-email-error' : 'delivery-email-help'}
                className="min-h-11 bg-background"
              />
              <p id="delivery-email-help" className="text-3xs leading-4 text-muted-foreground">
                {inferredEmail && !deliveryEmail ? `已从对话识别 ${inferredEmail}，也可以在这里覆盖。` : '用于保存草稿中的邮件交付配置。'}
              </p>
              {emailInputInvalid ? (
                <p id="delivery-email-error" className="text-3xs text-destructive">请输入有效邮箱地址</p>
              ) : null}
            </div> : null}

            <div className="rounded-xs border bg-background p-2 focus-within:ring-2 focus-within:ring-ring/40">
              <Textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void askAgent()
                  }
                }}
                className="min-h-20 resize-none border-0 bg-transparent p-2 shadow-none focus-visible:ring-0"
                placeholder={spec ? '继续补充：增加条件、修改来源或调整输出…' : '描述你希望项目持续完成的工作…'}
              />
              <div className="flex flex-wrap items-center gap-2 px-1">
                <span className="min-w-0 flex-1 font-mono text-3xs leading-4 text-muted-foreground">ENTER 发送 · SHIFT+ENTER 换行</span>
                <Button size="icon" className="min-h-11 min-w-11" onClick={() => void askAgent()} disabled={!input.trim() || generating} aria-label="发送需求">
                  <CornerDownLeft className="size-4" />
                </Button>
              </div>
            </div>

            <Button
              className="mt-3 min-h-11 w-full"
              variant="outline"
              onClick={() => void generateDraft()}
              disabled={!userRequirements.length || generating || pendingPatch !== null}
              aria-describedby="generation-readiness"
            >
              {generating ? <LoaderCircle className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
              {generating ? '正在生成方案' : spec ? '按当前要求更新方案' : '生成项目方案'}
            </Button>
            <p id="generation-readiness" className="mt-1.5 text-3xs leading-4 text-muted-foreground">
              {gaps.length ? `${gaps.length} 项 Capability Gap 不妨碍保存 Draft，但会阻止发布与运行。` : '当前 Draft 已具备发布前验证条件。'}
            </p>

            <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <Link href={workspaceId ? `/plugins?type=template&workspace=${workspaceId}` : '/plugins?type=template'} className="inline-flex min-h-11 items-center text-xs text-muted-foreground hover:text-foreground">
                从模板开始
              </Link>
              <button
                type="button"
                onClick={() => void persistProject(true)}
                disabled={!workspaceId || projectCreation.isPending}
                className="inline-flex min-h-11 items-center text-left text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                跳过 Agent，直接空白
              </button>
            </div>
          </div>
        </section>
      </>}>

        <section className="relative min-h-[560px] min-w-0 overflow-hidden bg-muted/10" aria-label="Agent 工作流方案">
          <div className="relative flex h-full min-w-0 flex-col">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-background/75 px-4 py-4 backdrop-blur sm:px-6">
              <div className="min-w-0">
                <div className="text-xs font-semibold">{spec ? 'Agent 生成的项目方案' : '等待需求'}</div>
                <div className="mt-0.5 break-words font-mono text-3xs text-muted-foreground">
                  {spec ? `${spec.nodes.length} NODES · ${spec.edges.length} CONNECTIONS` : 'DESCRIBE → GENERATE → CONFIRM'}
                </div>
              </div>
              {spec ? (
                <div className="flex items-center gap-2">
                  <Badge variant="outline">可编辑 Draft</Badge>
                  <Badge variant={workflowReadiness?.status === 'ready' ? 'secondary' : 'destructive'}>
                    {workflowReadiness?.status === 'ready' ? 'READY' : `${gaps.length} GAPS`}
                  </Badge>
                </div>
              ) : null}
            </div>

            {spec ? (
              <div className="flex min-h-0 flex-1 flex-col">
                <div className="grid gap-4 border-b bg-background/60 p-4 sm:grid-cols-[minmax(220px,420px)_minmax(0,1fr)] sm:p-5">
                  <label className="block space-y-2 text-xs">
                    <span className="font-medium">项目名称</span>
                    <Input value={name} onChange={(event) => handleNameChange(event.target.value)} className="min-h-11 rounded-xs bg-background" />
                  </label>
                  <div className="flex flex-wrap items-end gap-2 text-3xs text-muted-foreground">
                    <Badge variant="outline">{entryMode === 'scheduled' ? 'Schedule Trigger' : 'Manual Trigger'}</Badge>
                    <span>无频率时默认一次性查询；每次 Trigger firing 创建独立 Run。</span>
                  </div>
                </div>

                {gaps.length ? (
                  <div className="border-b border-amber-500/25 bg-amber-500/5 p-4 sm:px-5">
                    <div className="flex items-start gap-3">
                      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
                      <div className="min-w-0">
                        <div className="text-xs font-semibold">Capability Gap · 发布与运行已阻止</div>
                        <ul className="mt-2 grid gap-1 text-3xs leading-4 text-muted-foreground sm:grid-cols-2">
                          {gaps.map((gap) => <li key={gap.id}><span className="font-medium text-foreground">{gap.label}</span> — {gap.detail}</li>)}
                        </ul>
                      </div>
                    </div>
                  </div>
                ) : null}

                <div className="min-h-0 flex-1">
                  <AgentBuilderCanvas
                    spec={spec}
                    gapNodeIds={gapNodeIds}
                    onSpecChange={handleCanvasSpecChange}
                    onManualEdit={recordManualEdit}
                    resolvedDeliveryEmail={emailReady ? requirementState.finalEmail : undefined}
                  />
                </div>

                <div className="flex flex-col gap-3 border-t bg-background/90 p-4 backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-6">
                  <p className="max-w-md text-3xs leading-4 text-muted-foreground">
                    Draft 可随时保存。外部能力未配置、字段不兼容或 DAG 无效时，发布与运行保持阻止。
                  </p>
                  <div className="grid gap-2 sm:flex">
                    <Button variant="outline" disabled title={workflowReadiness?.canRun ? '请先保存并验证 Draft' : 'Capability Gap 尚未解决'}><Play className="size-4" />运行</Button>
                    <Button variant="outline" disabled title={workflowReadiness?.canPublish ? '请先保存并验证 Draft' : 'Capability Gap 尚未解决'}><Rocket className="size-4" />发布</Button>
                    <Button
                      className="min-h-11 w-full shrink-0 sm:w-auto"
                      onClick={() => void persistProject()}
                      disabled={!workspaceId || !name.trim() || projectCreation.isPending || durableSaving || generating || pendingPatch !== null}
                    >
                      <Save className="size-4" />
                      {projectCreation.isPending || durableSaving ? '正在保存' : durableDraft ? '打开正式编辑器' : '保存项目草稿'}
                    </Button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="grid flex-1 place-items-center p-6 sm:p-10">
                <div className="max-w-md text-center">
                  <div className="mx-auto grid size-14 place-items-center rounded-md border bg-background">
                    <Sparkles className="size-5 text-muted-foreground" />
                  </div>
                  <h3 className="mt-5 text-sm font-semibold">项目从一次对话开始</h3>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                    先说要查询或监控什么。未提供频率时创建 Manual Trigger；需要持续运行时补充定时要求。
                  </p>
                </div>
              </div>
            )}
          </div>
        </section>
      </ContextPageLayout>
    </PageContainer>
  )
}

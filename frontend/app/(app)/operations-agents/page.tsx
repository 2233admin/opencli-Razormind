'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowUp, Bell, Bot, CalendarClock, ChevronDown, CircleDot, Cloud, Code2, FileSearch, Pause, Play, Plus, Repeat2, Sparkles, Terminal } from 'lucide-react'
import { toast } from 'sonner'

import AgentAvatar from '@/components/smoothui/agent-avatar'
import { useAutomations, useCreateAutomation, useMyWorkspaces, useOperationsAgentActivity, useOperationsAgentDraft, useOperationsAgents, useOperationsAgentVersions, usePatchAutomation, usePublishOperationsAgentVersion, useStartOperationsAgentRun, useUpdateOperationsAgentDraft } from '@/lib/api/hooks'
import type { Automation, OperationsAgent, OperationsAgentMode } from '@/lib/api/types'
import { cn } from '@/lib/utils'
import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { Button, buttonVariants } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

const SUGGESTIONS = [
  { name: '每日运行简报', prompt: '汇总过去一天的运行、失败和待批准事项，给出需要关注的下一步。', icon: Bell, color: 'text-indigo-400', schedule: 'daily@08:00' },
  { name: '每周系统回顾', prompt: '回顾本周系统变化、风险、失败测试和待处理建议。', icon: Repeat2, color: 'text-violet-400', schedule: 'weekly@16:00' },
  { name: '异常跟进监控', prompt: '检查最近的异常活动，并将有证据的问题整理为待处理建议。', icon: FileSearch, color: 'text-emerald-400', schedule: 'weekdays@09:00' },
] as const

const EXECUTORS = [
  { id: 'codex', name: 'Codex', icon: Code2, color: 'text-sky-400' },
  { id: 'claude', name: 'Claude', icon: Sparkles, color: 'text-orange-400' },
  { id: 'chatcloud', name: 'ChatCloud', icon: Cloud, color: 'text-violet-400' },
  { id: 'custom', name: '自定义', icon: Terminal, color: 'text-emerald-400' },
] as const

const APPROVALS: Array<{ id: OperationsAgentMode; label: string; detail: string }> = [
  { id: 'observe_only', label: '仅观察', detail: '不提出或执行变更' },
  { id: 'suggest_changes', label: '建议需批准', detail: '送入 Inbox 后由人决定' },
  { id: 'low_risk_automatic', label: '低风险自动', detail: '白名单外仍需批准' },
]

function executorMeta(id: string) {
  return EXECUTORS.find((item) => item.id === id) ?? EXECUTORS[3]
}

function scheduleText(value: string) {
  const [kind, time] = value.split('@')
  return `${kind === 'daily' ? '每天' : kind === 'weekdays' ? '工作日' : kind === 'weekly' ? '每周' : kind}${time ? ` ${time}` : ''}`
}

const EMPTY_SCHEMA = { type: 'object', properties: {} }

function parseJsonObject(value: string, label: string) {
  const parsed = JSON.parse(value)
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(`${label} 必须是 JSON 对象`)
  return parsed as Record<string, unknown>
}

function publicRunSummary(payload: Record<string, unknown> | null) {
  if (!payload) return null
  const values = Object.entries(payload)
    .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${String(value)}`)
  return values.length ? values.join(' · ') : '已生成结构化执行结果，可在运行记录中审计。'
}

function ContractEditor({ workspaceId, agent }: { workspaceId: string; agent: OperationsAgent }) {
  const draft = useOperationsAgentDraft(workspaceId, agent.id)
  const versions = useOperationsAgentVersions(workspaceId, agent.id)
  const updateDraft = useUpdateOperationsAgentDraft()
  const publishVersion = usePublishOperationsAgentVersion()
  const [instructions, setInstructions] = useState('')
  const [inputSchema, setInputSchema] = useState('')
  const [outputSchema, setOutputSchema] = useState('')
  const [stateSchema, setStateSchema] = useState('')
  const [agentUrl, setAgentUrl] = useState('')
  const [workflow, setWorkflow] = useState('')
  const [dispatchTimeout, setDispatchTimeout] = useState(600)
  const [runtimeConfig, setRuntimeConfig] = useState('')
  const [reason, setReason] = useState('')

  useEffect(() => {
    if (!draft.data) return
    const contract = draft.data.model_configuration.agent_contract
    const binding = draft.data.model_configuration.runtime_binding
    setInstructions(draft.data.instructions)
    setInputSchema(JSON.stringify(contract?.input_schema ?? EMPTY_SCHEMA, null, 2))
    setOutputSchema(JSON.stringify(contract?.output_schema ?? EMPTY_SCHEMA, null, 2))
    setStateSchema(JSON.stringify(contract?.state_schema ?? EMPTY_SCHEMA, null, 2))
    setAgentUrl(binding?.agent_url ?? '')
    setWorkflow(binding?.workflow ?? '')
    setDispatchTimeout(binding?.dispatch_timeout_seconds ?? 600)
    setRuntimeConfig(JSON.stringify(binding?.config ?? {}, null, 2))
  }, [draft.data])

  async function saveDraft() {
    if (!draft.data) return
    try {
      await updateDraft.mutateAsync({
        workspaceId,
        agentId: agent.id,
        data: {
          revision: draft.data.revision,
          instructions: instructions.trim(),
          model_configuration: {
            ...draft.data.model_configuration,
            agent_contract: {
              schema_version: 'agent.contract.v1',
              input_schema: parseJsonObject(inputSchema, 'Input schema'),
              output_schema: parseJsonObject(outputSchema, 'Output schema'),
              state_schema: parseJsonObject(stateSchema, 'State schema'),
            },
            runtime_binding: {
              schema_version: 'agent.runtime-binding.v1',
              agent_url: agentUrl.trim(),
              runtime: 'pi',
              workflow: workflow.trim(),
              dispatch_timeout_seconds: dispatchTimeout,
              config: parseJsonObject(runtimeConfig, 'Runtime config'),
            },
          },
          tool_configuration: draft.data.tool_configuration,
        },
      })
      toast.success('Contract 草稿已保存')
    } catch (error) {
      if ((error as Error & { status?: number }).status === 409) {
        await draft.refetch()
        toast.error('草稿已被其他人修改，已重新加载最新 revision')
        return
      }
      toast.error(error instanceof Error ? error.message : '保存失败')
    }
  }

  async function publish() {
    if (!reason.trim()) return
    try {
      await publishVersion.mutateAsync({ workspaceId, agentId: agent.id, reason: reason.trim() })
      setReason('')
      toast.success('Contract 版本已发布')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '发布失败')
    }
  }

  const currentPublishedVersion = Math.max(agent.current_published_version ?? 0, ...(versions.data?.map((version) => version.version) ?? [0]))
  if (draft.isLoading) return <div className="p-6"><LoadingState rows={4} /></div>
  if (draft.isError) return <div className="p-6"><ErrorState message={(draft.error as Error)?.message} hint={BACKEND_HINT} /></div>

  return (
    <div className="grid min-h-0 flex-1 md:grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-h-0 overflow-y-auto bg-[#090a0b] p-5">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div><h3 className="text-sm font-medium">Agent Contract</h3><p className="mt-1 text-xs text-muted-foreground">Draft r{draft.data?.revision ?? '—'} · 当前发布 v{currentPublishedVersion || '—'}</p></div>
          <Button size="sm" onClick={() => void saveDraft()} disabled={!instructions.trim() || !agentUrl.trim() || !workflow.trim() || updateDraft.isPending}>保存草稿</Button>
        </div>
        <div className="space-y-5">
          <label className="block space-y-1.5 text-xs text-muted-foreground">Instructions<Textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} className="min-h-36 resize-y text-sm text-foreground" placeholder="描述智能体的职责、边界和执行要求" /></label>
          <div className="grid gap-4 lg:grid-cols-3">
            <label className="block space-y-1.5 text-xs text-muted-foreground">Input schema<Textarea value={inputSchema} onChange={(event) => setInputSchema(event.target.value)} spellCheck={false} className="min-h-64 resize-y font-mono text-xs text-foreground" /></label>
            <label className="block space-y-1.5 text-xs text-muted-foreground">Output schema<Textarea value={outputSchema} onChange={(event) => setOutputSchema(event.target.value)} spellCheck={false} className="min-h-64 resize-y font-mono text-xs text-foreground" /></label>
            <label className="block space-y-1.5 text-xs text-muted-foreground">State schema<Textarea value={stateSchema} onChange={(event) => setStateSchema(event.target.value)} spellCheck={false} className="min-h-64 resize-y font-mono text-xs text-foreground" /></label>
          </div>
          <details className="rounded-lg border border-white/[0.08] px-4 py-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">Runtime Binding 高级配置</summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-4">
              <label className="space-y-1.5 text-xs text-muted-foreground">Agent URL<Input type="url" value={agentUrl} onChange={(event) => setAgentUrl(event.target.value)} placeholder="https://agent.example.com" /></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">Runtime<Input value="pi" readOnly aria-readonly="true" /></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">Workflow<Input value={workflow} onChange={(event) => setWorkflow(event.target.value)} placeholder="default" /></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">超时（秒）<Input type="number" min={1} max={3600} value={dispatchTimeout} onChange={(event) => setDispatchTimeout(Number(event.target.value))} /></label>
            </div>
            <label className="mt-4 block space-y-1.5 text-xs text-muted-foreground">Task config（仅 timeout_seconds）<Textarea value={runtimeConfig} onChange={(event) => setRuntimeConfig(event.target.value)} spellCheck={false} className="min-h-32 resize-y font-mono text-xs text-foreground" /></label>
          </details>
        </div>
      </div>
      <aside className="min-h-0 overflow-y-auto border-l border-white/[0.08] bg-white/[0.015] p-5">
        <h3 className="text-xs font-medium text-muted-foreground">发布新版本</h3>
        <Textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="发布原因（必填）" className="mt-3 min-h-20 resize-y text-sm" />
        <Button className="mt-3 w-full" size="sm" onClick={() => void publish()} disabled={!reason.trim() || publishVersion.isPending}>发布 Contract</Button>
        <div className="mt-7 border-t border-white/[0.08] pt-5">
          <h3 className="text-xs font-medium text-muted-foreground">版本历史</h3>
          {versions.isLoading ? <div className="mt-3 text-xs text-muted-foreground">加载中…</div> : versions.isError ? <p className="mt-3 text-xs text-destructive">{(versions.error as Error)?.message}</p> : !versions.data?.length ? <p className="mt-3 text-xs text-muted-foreground">尚未发布版本</p> : <div className="mt-3 space-y-2">{versions.data.map((version) => <div key={version.version} className="rounded-lg border border-white/[0.08] p-3"><div className="flex items-center justify-between text-xs"><span className="font-medium text-foreground">v{version.version}</span><span className="text-muted-foreground">Draft r{version.draft_revision}</span></div><p className="mt-2 text-xs leading-5 text-muted-foreground">{version.reason}</p><p className="mt-1 text-[11px] text-muted-foreground">{new Date(version.created_at).toLocaleString()}</p></div>)}</div>}
        </div>
      </aside>
    </div>
  )
}

export default function OperationsAgentsPage() {
  const workspaces = useMyWorkspaces()
  const [workspaceId, setWorkspaceId] = useState<string | null>(null)
  const [view, setView] = useState<'automations' | 'agents'>('automations')
  const automations = useAutomations(workspaceId)
  const agents = useOperationsAgents(workspaceId)
  const activity = useOperationsAgentActivity(workspaceId)
  const createAutomation = useCreateAutomation()
  const patchAutomation = usePatchAutomation()
  const startRunMutation = useStartOperationsAgentRun()
  const [open, setOpen] = useState(false)
  const [selectedAgent, setSelectedAgent] = useState<OperationsAgent | null>(null)
  const [agentDetailView, setAgentDetailView] = useState<'activity' | 'contract'>('activity')
  const [automationDraft, setAutomationDraft] = useState('')
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [precheck, setPrecheck] = useState('')
  const [executor, setExecutor] = useState('codex')
  const [projectPath, setProjectPath] = useState('')
  const [branch, setBranch] = useState('main')
  const [scheduleKind, setScheduleKind] = useState('weekdays')
  const [time, setTime] = useState('09:00')
  const [sessionMode, setSessionMode] = useState<'fresh' | 'reuse'>('fresh')
  const [approvalMode, setApprovalMode] = useState<OperationsAgentMode>('suggest_changes')
  const [runTargetType, setRunTargetType] = useState('manual')
  const [runTargetId, setRunTargetId] = useState('')
  const [runInput, setRunInput] = useState('{}')
  const [runState, setRunState] = useState('{}')

  useEffect(() => {
    if (!workspaceId && workspaces.data?.length) setWorkspaceId(workspaces.data[0].id)
  }, [workspaceId, workspaces.data])

  const latestRun = useMemo(() => new Map(activity.data?.map((run) => [run.operations_agent_id, run]) ?? []), [activity.data])

  function startCreate(preset?: (typeof SUGGESTIONS)[number]) {
    setName(preset?.name ?? '')
    setPrompt(preset?.prompt ?? '')
    if (preset) {
      const [kind, presetTime] = preset.schedule.split('@')
      setScheduleKind(kind)
      setTime(presetTime)
    }
    setOpen(true)
  }

  function configureDraft() {
    const draft = automationDraft.trim()
    if (!draft) return
    setName('')
    setPrompt(draft)
    setOpen(true)
  }

  async function submitCreate() {
    if (!workspaceId) return
    try {
      await createAutomation.mutateAsync({ workspaceId, data: {
        name: name.trim(), prompt: prompt.trim(), precheck: precheck.trim() || null,
        executor, schedule: `${scheduleKind}@${time}`, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        session_mode: sessionMode, approval_mode: approvalMode,
        project: { path: projectPath.trim() || null, branch: branch.trim() || null }, enabled: true,
      } })
      setOpen(false)
      toast.success('自动化已创建')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '创建失败')
    }
  }

  async function toggleAutomation(automation: Automation) {
    if (!workspaceId) return
    try {
      await patchAutomation.mutateAsync({ workspaceId, automationId: automation.id, data: { enabled: !automation.enabled } })
      toast.success(automation.enabled ? '自动化已暂停' : '自动化已恢复')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '操作失败')
    }
  }

  async function startRun() {
    if (!workspaceId || !selectedAgent || !runTargetType.trim() || !runTargetId.trim()) return
    try {
      await startRunMutation.mutateAsync({
        workspaceId,
        agentId: selectedAgent.id,
        data: {
          target_resource_type: runTargetType.trim(),
          target_resource_id: runTargetId.trim(),
          input_payload: parseJsonObject(runInput, 'Input payload'),
          state_payload: parseJsonObject(runState, 'State payload'),
        },
      })
      toast.success('Run 已提交，Fleet 预检通过')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Run 启动失败')
    }
  }

  if (workspaces.isLoading) return <div className="p-8"><LoadingState rows={3} /></div>
  if (workspaces.isError) return <div className="p-8"><ErrorState message={(workspaces.error as Error)?.message} hint={BACKEND_HINT} /></div>
  if (!workspaces.data?.length) return <div className="p-8"><EmptyState title="尚未加入 Workspace" description="加入 Workspace 后才能使用自动化和智能体。" /></div>

  return (
    <div className="min-h-full bg-[#0f1012] text-foreground">
      <div className="mx-auto w-full max-w-5xl px-6 py-12 sm:px-10 lg:py-16">
        <header className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <h1 className="text-3xl font-normal tracking-[-0.025em] sm:text-4xl">自动化与智能体</h1>
            <p className="mt-3 text-base text-muted-foreground">安排任务，观察智能体正在做什么</p>
          </div>
          <label className="relative"><select value={workspaceId ?? ''} onChange={(event) => setWorkspaceId(event.target.value)} className="h-9 appearance-none rounded-lg border bg-background py-1 pl-3 pr-8 text-xs" aria-label="选择 Workspace">{workspaces.data.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}</select><ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 size-4 text-muted-foreground" /></label>
        </header>

        <div className="mt-10 flex items-center gap-1 border-b border-white/[0.08]">
          <button type="button" onClick={() => setView('automations')} className={cn('border-b-2 px-4 py-3 text-sm transition-colors', view === 'automations' ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}><CalendarClock className="mr-2 inline size-4" />自动化</button>
          <button type="button" onClick={() => setView('agents')} className={cn('border-b-2 px-4 py-3 text-sm transition-colors', view === 'agents' ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}><Bot className="mr-2 inline size-4" />智能体</button>
        </div>

        {view === 'automations' ? (
          <div>
            <section className="mt-10">
              <div className="mx-auto max-w-3xl">
                <h2 className="text-xl font-medium tracking-[-0.015em]">想让系统定期做什么？</h2>
                <p className="mt-1 text-sm text-muted-foreground">直接描述任务，再确认日程、智能体和审批方式。</p>
                <div className="mt-5 flex items-end gap-2 rounded-2xl border border-white/[0.1] bg-white/[0.04] p-2 pl-4 shadow-[0_14px_45px_rgba(0,0,0,.18)] focus-within:border-white/[0.2]">
                  <Textarea value={automationDraft} onChange={(event) => setAutomationDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); configureDraft() } }} placeholder="例如：每个工作日检查失败任务，把需要处理的项目送到 Inbox" aria-label="描述自动化任务" className="min-h-12 resize-none border-0 bg-transparent px-0 py-3 shadow-none focus-visible:ring-0" />
                  <Button size="icon" className="mb-0.5 rounded-full" aria-label="配置自动化" disabled={!automationDraft.trim()} onClick={configureDraft}><ArrowUp /></Button>
                </div>
                <div className="mt-7 space-y-1">{SUGGESTIONS.map((item) => { const Icon = item.icon; return <button key={item.name} type="button" onClick={() => startCreate(item)} className="group flex w-full items-start gap-4 rounded-xl px-3 py-3 text-left transition-colors hover:bg-white/[0.04]"><Icon className={`mt-0.5 size-5 ${item.color}`} /><span className="flex-1"><span className="block text-sm font-medium">{item.name} <span className="ml-2 font-normal text-muted-foreground">{scheduleText(item.schedule)}</span></span><span className="mt-1 block text-sm text-muted-foreground">{item.prompt}</span></span><Plus className="mt-2 size-4 opacity-0 transition-opacity group-hover:opacity-100" /></button> })}</div>
              </div>
            </section>
            <section className="mt-12">
              <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-medium text-muted-foreground">我的自动化</h2><Button variant="ghost" size="sm" onClick={() => startCreate()}><Plus />手动配置</Button></div>
              {automations.isLoading ? <LoadingState rows={3} /> : automations.isError ? <ErrorState message={(automations.error as Error)?.message} hint={BACKEND_HINT} /> : !automations.data?.length ? <EmptyState title="还没有自动化" description="使用模板或创建一个新的定时任务。" /> : <div className="divide-y divide-white/[0.06]">{automations.data.map((automation) => { const meta = executorMeta(automation.executor); const Icon = meta.icon; return <div key={automation.id} className="flex items-start gap-4 rounded-lg px-3 py-4 hover:bg-white/[0.03]"><span className={cn('mt-0.5 flex size-7 items-center justify-center rounded-md bg-white/[0.05]', meta.color)}><Icon className="size-4" /></span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-3"><span className="text-sm font-medium">{automation.name}</span><span className={cn('rounded-full px-2 py-0.5 text-[11px]', automation.enabled ? 'bg-white/[0.06] text-muted-foreground' : 'bg-amber-400/10 text-amber-300')}>{automation.enabled ? '等待首次运行' : '已暂停'}</span><span className="text-xs text-muted-foreground">{scheduleText(automation.schedule)}</span><span className="text-xs text-muted-foreground">{meta.name}</span></div><p className="mt-1 truncate text-sm text-muted-foreground">{automation.prompt}</p></div><Button size="icon-sm" variant="ghost" aria-label={automation.enabled ? `暂停 ${automation.name}` : `恢复 ${automation.name}`} onClick={() => void toggleAutomation(automation)}>{automation.enabled ? <Pause /> : <Play />}</Button></div> })}</div>}
            </section>
          </div>
        ) : (
          <section className="mt-10">
            <div className="mb-7 flex items-start justify-between gap-4"><div><h2 className="text-xl font-medium tracking-[-0.015em]">智能体正在做什么</h2><p className="mt-1 text-sm text-muted-foreground">状态每 5 秒更新；需要你决定的动作会进入 Inbox。</p></div><Link href="/agents" className={buttonVariants()}><Plus />添加智能体</Link></div>
            {agents.isLoading ? <LoadingState rows={3} /> : agents.isError ? <ErrorState message={(agents.error as Error)?.message} hint={BACKEND_HINT} /> : !agents.data?.length ? <EmptyState title="还没有智能体" description="添加一个智能体后，它的当前任务和最近活动会显示在这里。" /> : <div className="divide-y divide-white/[0.07] border-y border-white/[0.07]">{agents.data.map((agent) => { const run = latestRun.get(agent.id); const working = run?.status === 'running' || run?.status === 'queued'; return <article key={agent.id}><button type="button" onClick={() => { setSelectedAgent(agent); setAgentDetailView('activity') }} className="group flex w-full items-start gap-4 px-2 py-5 text-left transition-colors hover:bg-white/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/30"><div className="relative size-10 shrink-0"><AgentAvatar seed={agent.id} size={40} />{working ? <span className="absolute -right-0.5 -top-0.5 size-2.5 animate-pulse rounded-full border-2 border-[#0f1012] bg-emerald-400" /> : null}</div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-x-3 gap-y-1"><h3 className="text-sm font-medium">{agent.name}</h3><span className={cn('text-xs', working ? 'text-emerald-400' : agent.disabled ? 'text-amber-300' : 'text-muted-foreground')}>{agent.disabled ? '已停用' : working ? '工作中' : '空闲'}</span></div><p className="mt-1 text-sm text-muted-foreground">{run ? `${run.target_resource_type} · ${run.target_resource_id}` : agent.description || '等待任务'}</p>{run ? <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground"><CircleDot className="size-3" />{run.trigger_type} · {run.status}</div> : <div className="mt-3 text-xs text-muted-foreground">暂无进行中的任务</div>}</div><ChevronDown className="mt-2 size-4 -rotate-90 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" /></button></article> })}</div>}
          </section>
        )}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-4xl">
          <DialogHeader><DialogTitle>创建自动化</DialogTitle><DialogDescription>网页和对话 AI 都通过同一个 Automation API 创建此配置。</DialogDescription></DialogHeader>
          <div className="grid gap-5 py-2">
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="自动化名称" className="text-base font-medium" autoFocus />
            <label className="space-y-1.5 text-xs text-muted-foreground">提示词<Textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="每次运行要完成什么？" className="min-h-40 resize-y text-sm" /></label>
            <fieldset><legend className="mb-2 text-xs text-muted-foreground">选择智能体</legend><div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{EXECUTORS.map((item) => { const Icon = item.icon; return <button key={item.id} type="button" aria-pressed={executor === item.id} onClick={() => setExecutor(item.id)} className={cn('flex items-center gap-2 rounded-lg border px-3 py-3 text-left text-sm', executor === item.id ? 'border-foreground bg-white/[0.06]' : 'border-white/[0.08] hover:bg-white/[0.03]')}><Icon className={cn('size-4', item.color)} />{item.name}</button> })}</div></fieldset>
            <div className="grid gap-4 sm:grid-cols-2"><label className="space-y-1.5 text-xs text-muted-foreground">项目路径<Input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="/workspace/project" /></label><label className="space-y-1.5 text-xs text-muted-foreground">基础分支<Input value={branch} onChange={(event) => setBranch(event.target.value)} /></label></div>
            <div className="grid gap-4 sm:grid-cols-3"><label className="space-y-1.5 text-xs text-muted-foreground">日程<select value={scheduleKind} onChange={(event) => setScheduleKind(event.target.value)} className="h-9 w-full rounded-lg border bg-background px-3 text-sm"><option value="hourly">每小时</option><option value="daily">每天</option><option value="weekdays">工作日</option><option value="weekly">每周</option></select></label><label className="space-y-1.5 text-xs text-muted-foreground">时间<Input type="time" value={time} onChange={(event) => setTime(event.target.value)} disabled={scheduleKind === 'hourly'} /></label><label className="space-y-1.5 text-xs text-muted-foreground">会话<select value={sessionMode} onChange={(event) => setSessionMode(event.target.value as 'fresh' | 'reuse')} className="h-9 w-full rounded-lg border bg-background px-3 text-sm"><option value="fresh">每次新会话</option><option value="reuse">重复利用会话</option></select></label></div>
            <fieldset><legend className="mb-2 text-xs text-muted-foreground">审批方式</legend><div className="grid gap-2 sm:grid-cols-3">{APPROVALS.map((item) => <button key={item.id} type="button" onClick={() => setApprovalMode(item.id)} className={cn('rounded-lg border p-3 text-left', approvalMode === item.id ? 'border-foreground bg-white/[0.06]' : 'border-white/[0.08]')}><span className="block text-sm font-medium">{item.label}</span><span className="mt-1 block text-xs text-muted-foreground">{item.detail}</span></button>)}</div></fieldset>
            <details className="rounded-lg border border-white/[0.08] px-4 py-3"><summary className="cursor-pointer text-sm text-muted-foreground">高级设置</summary><label className="mt-4 block space-y-1.5 text-xs text-muted-foreground"><Terminal className="mr-1 inline size-3" />预检查<Input value={precheck} onChange={(event) => setPrecheck(event.target.value)} placeholder="可选：运行前检查命令" className="font-mono" /></label></details>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>取消</Button><Button onClick={() => void submitCreate()} disabled={!name.trim() || !prompt.trim() || createAutomation.isPending}><Plus />创建</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(selectedAgent)} onOpenChange={(nextOpen) => { if (!nextOpen) setSelectedAgent(null) }}>
        <DialogContent className="flex h-[82vh] max-h-[820px] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl">
          {selectedAgent ? <>
            <DialogHeader className="border-b border-white/[0.08] px-6 py-4">
              <div className="flex items-center gap-3 pr-8"><AgentAvatar seed={selectedAgent.id} size={36} /><div className="min-w-0"><DialogTitle className="flex items-center gap-2 text-base"><span className="truncate">{selectedAgent.name}</span><span className={cn('text-xs font-normal', selectedAgent.disabled ? 'text-amber-300' : latestRun.get(selectedAgent.id)?.status === 'running' ? 'text-emerald-400' : 'text-muted-foreground')}>{selectedAgent.disabled ? '已停用' : latestRun.get(selectedAgent.id)?.status === 'running' ? '工作中' : '空闲'}</span></DialogTitle><DialogDescription className="mt-0.5">CLI Agent 会话 · {APPROVALS.find((item) => item.id === selectedAgent.current_profile.mode)?.label ?? selectedAgent.current_profile.mode}</DialogDescription></div></div>
              <div className="mt-3 flex gap-1"><button type="button" onClick={() => setAgentDetailView('activity')} className={cn('rounded-md px-3 py-1.5 text-xs', agentDetailView === 'activity' ? 'bg-white/[0.08] text-foreground' : 'text-muted-foreground hover:text-foreground')}>活动</button><button type="button" onClick={() => setAgentDetailView('contract')} className={cn('rounded-md px-3 py-1.5 text-xs', agentDetailView === 'contract' ? 'bg-white/[0.08] text-foreground' : 'text-muted-foreground hover:text-foreground')}>Contract</button></div>
            </DialogHeader>
            {agentDetailView === 'activity' ? <div className="grid min-h-0 flex-1 md:grid-cols-[minmax(0,1fr)_250px]">
              <div className="flex min-h-0 flex-col bg-[#090a0b]">
                <div className="flex items-center gap-2 border-b border-white/[0.06] px-5 py-2 font-mono text-[11px] text-muted-foreground"><Terminal className="size-3" />SESSION OUTPUT</div>
                <div className="min-h-0 flex-1 overflow-y-auto p-5 font-mono text-xs leading-6">
                  {(activity.data ?? []).filter((run) => run.operations_agent_id === selectedAgent.id).length ? <div className="space-y-4">{(activity.data ?? []).filter((run) => run.operations_agent_id === selectedAgent.id).map((run) => <article key={run.id} className="rounded-lg border border-white/[0.08] bg-white/[0.025] p-4"><div className="flex items-center justify-between gap-3"><span className={cn('font-medium', run.status === 'failed' ? 'text-red-300' : run.status === 'completed' ? 'text-emerald-400' : 'text-sky-300')}>{run.status === 'queued' ? '等待执行' : run.status === 'running' ? '正在执行' : run.status === 'completed' ? '已完成' : run.status === 'paused' ? '等待确认' : run.status === 'cancelled' ? '已取消' : '执行失败'}</span><time className="text-muted-foreground">{new Date(run.updated_at).toLocaleString()}</time></div><p className="mt-2 text-sm text-white/85">目标：{run.target_resource_type} · {run.target_resource_id}</p>{run.error_message ? <div className="mt-3 rounded-md bg-red-400/10 px-3 py-2 text-red-200">{run.error_message}<p className="mt-1 text-red-200/70">检查目标状态后可以重新启动。</p></div> : null}{publicRunSummary(run.output_payload) ? <div className="mt-3 rounded-md bg-emerald-400/10 px-3 py-2 text-emerald-100">结果：{publicRunSummary(run.output_payload)}</div> : null}</article>)}</div> : <div className="flex h-full min-h-56 flex-col items-center justify-center text-center font-sans"><Terminal className="mb-3 size-6 text-white/25" /><p className="text-sm text-white/65">还没有执行活动</p><p className="mt-1 max-w-xs text-xs leading-5 text-white/35">智能体收到任务后，这里会显示目标、当前状态和结果摘要。</p></div>}
                </div>
                <div className="border-t border-white/[0.06] p-3">
                  <details className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2">
                    <summary className="cursor-pointer text-xs text-muted-foreground">启动真实 Run</summary>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <Input value={runTargetType} onChange={(event) => setRunTargetType(event.target.value)} placeholder="资源类型" aria-label="Run 资源类型" />
                      <Input value={runTargetId} onChange={(event) => setRunTargetId(event.target.value)} placeholder="资源 ID" aria-label="Run 资源 ID" />
                      <Textarea value={runInput} onChange={(event) => setRunInput(event.target.value)} spellCheck={false} className="min-h-24 font-mono text-xs" aria-label="Run input payload" />
                      <Textarea value={runState} onChange={(event) => setRunState(event.target.value)} spellCheck={false} className="min-h-24 font-mono text-xs" aria-label="Run state payload" />
                    </div>
                    <Button className="mt-3" size="sm" onClick={() => void startRun()} disabled={!runTargetType.trim() || !runTargetId.trim() || startRunMutation.isPending}><Play />启动并执行 Fleet 预检</Button>
                  </details>
                </div>
              </div>
              <aside className="border-l border-white/[0.08] bg-white/[0.015] p-5 text-xs"><div className="text-muted-foreground">职责</div><p className="mt-2 text-sm leading-6 text-foreground">{selectedAgent.description || '尚未填写职责说明'}</p><div className="mt-6 text-muted-foreground">权限模式</div><p className="mt-2 text-sm text-foreground">{APPROVALS.find((item) => item.id === selectedAgent.current_profile.mode)?.label}</p><p className="mt-1 leading-5 text-muted-foreground">Profile v{selectedAgent.current_profile.version} · {selectedAgent.current_profile.reason}</p><Link href="/agents" className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'mt-6 w-full')}>配置智能体</Link></aside>
            </div> : workspaceId ? <ContractEditor workspaceId={workspaceId} agent={selectedAgent} /> : null}
          </> : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}

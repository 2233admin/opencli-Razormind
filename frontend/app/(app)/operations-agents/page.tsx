'use client'

import { useEffect, useMemo, useState } from 'react'
import { ArrowUp, Bell, Bot, CalendarClock, ChevronDown, CircleDot, FileSearch, Pause, Play, Plus, Repeat2, Terminal } from 'lucide-react'
import { toast } from 'sonner'

import AgentAvatar from '@/components/smoothui/agent-avatar'
import SwitchboardCard from '@/components/smoothui/switchboard-card'
import { useAutomations, useCreateAutomation, useGovernedWorkspaces, useInstallAutomationStarters, useOperationsAgentActivity, useOperationsAgentDraft, useOperationsAgents, useOperationsAgentVersion, useOperationsAgentVersions, usePatchAutomation, usePublishOperationsAgentVersion, useStartOperationsAgentRun, useUpdateOperationsAgentDraft } from '@/lib/api/hooks'
import type { AgentRuntimeBindingV2, Automation, OperationsAgent, OperationsAgentMode } from '@/lib/api/types'
import { cn } from '@/lib/utils'
import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

const SUGGESTIONS = [
  { name: '每日运行简报', prompt: '汇总过去一天的运行、失败和待批准事项，给出需要关注的下一步。', icon: Bell, color: 'text-indigo-400', schedule: 'daily@08:00' },
  { name: '每周系统回顾', prompt: '回顾本周系统变化、风险、失败测试和待处理建议。', icon: Repeat2, color: 'text-violet-400', schedule: 'weekly@16:00' },
  { name: '异常跟进监控', prompt: '检查最近的异常活动，并将有证据的问题整理为待处理建议。', icon: FileSearch, color: 'text-emerald-400', schedule: 'weekdays@09:00' },
] as const

const AGENT_STARTERS = [
  {
    name: '运行简报 Agent',
    prompt: 'Prepare a concise daily run brief from the latest workspace activity and open work.',
    schedule: 'daily@09:00',
    subtitle: '每天汇总运行、失败与待批准事项',
    executor: 'codex',
    pattern: [0, 1, 2, 18, 19, 20, 36, 37, 38, 54, 55, 56, 72, 73, 74],
  },
  {
    name: '系统回顾 Agent',
    prompt: 'Review the workspace system state, summarize trends, and identify actionable improvements.',
    schedule: 'weekly@monday@09:00',
    subtitle: '每周整理变化、风险与待处理建议',
    executor: 'codex',
    pattern: [4, 5, 6, 22, 23, 24, 40, 41, 42, 58, 59, 60, 76, 77, 78],
  },
  {
    name: '异常跟进 Agent',
    prompt: 'Review unresolved anomalies, gather evidence, and propose the next safe follow-up actions.',
    schedule: 'on_anomaly',
    subtitle: '异常出现时检查并生成证据化建议',
    executor: 'codex',
    pattern: [8, 9, 10, 26, 27, 28, 44, 45, 46, 62, 63, 64, 80, 81, 82],
  },
] as const

type AgentStarterInput = {
  name: string
  prompt: string
  schedule: string
  executor?: string
}

const EXECUTORS = [
  { id: 'codex', name: 'Codex', icon: Code2, color: 'text-sky-400' },
  { id: 'claude', name: 'Claude', icon: Sparkles, color: 'text-orange-400' },
  { id: 'chatcloud', name: 'ChatCloud', icon: Cloud, color: 'text-violet-400' },
  { id: 'custom', name: '自定义', icon: Terminal, color: 'text-emerald-400' },
] as const

type AgentStarterInput = {
  name: string
  prompt: string
  schedule: string
}

function scheduleText(value: string) {
  const [kind, qualifier, time] = value.split('@')
  if (kind === 'on_anomaly') return '检测到异常时'
  if (kind === 'weekly') {
    const weekdays: Record<string, string> = {
      monday: '一', tuesday: '二', wednesday: '三', thursday: '四', friday: '五', saturday: '六', sunday: '日',
    }
    return `每周${weekdays[qualifier] ?? qualifier}${time ? ` ${time}` : ''}`
  }
  const label = kind === 'daily' ? '每天' : kind === 'weekdays' ? '工作日' : kind === 'hourly' ? '每小时' : kind
  return `${label}${qualifier ? ` ${qualifier}` : ''}`
}

const EMPTY_SCHEMA = { type: 'object', properties: {} }

function parseJsonObject(value: string, label: string) {
  const parsed = JSON.parse(value)
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(`${label} 必须是 JSON 对象`)
  return parsed as Record<string, unknown>
}

function parseJsonArray(value: string, label: string) {
  const parsed = JSON.parse(value)
  if (!Array.isArray(parsed) || parsed.some((item) => !item || Array.isArray(item) || typeof item !== 'object')) {
    throw new Error(`${label} 必须是 JSON 对象数组`)
  }
  return parsed as Array<Record<string, unknown>>
}

function parseIdentifierList(value: string) {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))]
}

function ContractEditor({ workspaceId, agent }: { workspaceId: string; agent: OperationsAgent }) {
  const draft = useOperationsAgentDraft(workspaceId, agent.id)
  const versions = useOperationsAgentVersions(workspaceId, agent.id)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const selectedVersionQuery = useOperationsAgentVersion(workspaceId, agent.id, selectedVersion)
  const updateDraft = useUpdateOperationsAgentDraft()
  const nodes = useNodes()
  const publishVersion = usePublishOperationsAgentVersion()
  const [instructions, setInstructions] = useState('')
  const [role, setRole] = useState('operations_agent')
  const [requiredCapabilities, setRequiredCapabilities] = useState('streaming')
  const [toolPolicy, setToolPolicy] = useState('{}')
  const [budget, setBudget] = useState('{}')
  const [qualityGates, setQualityGates] = useState('[]')
  const [evidenceRequirements, setEvidenceRequirements] = useState('')
  const [inputSchema, setInputSchema] = useState('')
  const [outputSchema, setOutputSchema] = useState('')
  const [stateSchema, setStateSchema] = useState('')
  const [agentUrl, setAgentUrl] = useState('')
  const [runtime, setRuntime] = useState<AgentRuntimeBindingV2['preferred_runtimes'][number]>('pi')
  const [workflow, setWorkflow] = useState('')
  const [dispatchTimeout, setDispatchTimeout] = useState(1800)
  const [runtimeConfig, setRuntimeConfig] = useState('')
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [authProfile, setAuthProfile] = useState('')
  const [reason, setReason] = useState('')
  const runtimeNodes = useMemo(
    () => [...(nodes.data?.data ?? [])].reverse().filter(
      (node) => node.protocol === 'ws' && node.status === 'online' && Object.keys(node.runtime_capabilities ?? {}).length,
    ),
    [nodes.data],
  )
  const selectedRuntimeNode = runtimeNodes.find((node) => node.url === agentUrl)
  const runtimeOptions = selectedRuntimeNode
    ? Object.keys(selectedRuntimeNode.runtime_capabilities ?? {})
    : [...new Set(runtimeNodes.flatMap((node) => Object.keys(node.runtime_capabilities ?? {})))]
  useEffect(() => {
    if (!draft.data) return
    const contract = draft.data.model_configuration.agent_contract
    const binding = draft.data.model_configuration.runtime_binding
    setInstructions(draft.data.instructions)
    setRole(contract?.role ?? 'operations_agent')
    setRequiredCapabilities((contract?.required_capabilities ?? ['streaming']).join(', '))
    setToolPolicy(JSON.stringify(contract?.tool_policy ?? {}, null, 2))
    setBudget(JSON.stringify(contract?.budget ?? {}, null, 2))
    setQualityGates(JSON.stringify(contract?.quality_gates ?? [], null, 2))
    setEvidenceRequirements((contract?.evidence_requirements ?? []).join(', '))
    setInputSchema(JSON.stringify(contract?.input_schema ?? EMPTY_SCHEMA, null, 2))
    setOutputSchema(JSON.stringify(contract?.output_schema ?? EMPTY_SCHEMA, null, 2))
    setStateSchema(JSON.stringify(contract?.state_schema ?? EMPTY_SCHEMA, null, 2))
    setAgentUrl(binding?.preferred_agent_urls?.[0] ?? '')
    setRuntime(binding?.preferred_runtimes?.[0] ?? 'pi')
    setWorkflow(binding?.workflow ?? '')
    setDispatchTimeout(binding?.dispatch_timeout_seconds ?? 1800)
    setRuntimeConfig(JSON.stringify(binding?.config ?? { timeout_seconds: 1800 }, null, 2))
  }, [draft.data])


  async function saveDraft() {
    if (!draft.data) return
    if (Boolean(provider.trim()) !== Boolean(model.trim())) {
      toast.error('Provider 与 Model 必须同时填写或同时留空')
      return
    }
    try {
      const binding = draft.data.model_configuration.runtime_binding
      await updateDraft.mutateAsync({
        workspaceId,
        agentId: agent.id,
        data: {
          revision: draft.data.revision,
          instructions: instructions.trim(),
          model_configuration: {
            ...draft.data.model_configuration,
            agent_contract: {
              schema_version: 'agent.contract.v2',
              role: role.trim(),
              input_schema: parseJsonObject(inputSchema, 'Input schema'),
              output_schema: parseJsonObject(outputSchema, 'Output schema'),
              state_schema: parseJsonObject(stateSchema, 'State schema'),
              required_capabilities: parseIdentifierList(requiredCapabilities),
              tool_policy: parseJsonObject(toolPolicy, 'Tool policy'),
              budget: parseJsonObject(budget, 'Budget'),
              quality_gates: parseJsonArray(qualityGates, 'Quality gates'),
              evidence_requirements: parseIdentifierList(evidenceRequirements),
            },
            runtime_binding: {
              schema_version: 'agent.runtime-binding.v2',
              workflow: workflow.trim(),
              preferred_agent_urls: agentUrl.trim() ? [agentUrl.trim()] : [],
              preferred_runtimes: runtime ? [runtime] : [],
              model_binding: provider.trim() && model.trim() ? {
                schema_version: 'agent.model-binding.v1',
                provider: provider.trim(),
                model: model.trim(),
                auth_profile: authProfile.trim() || null,
              } : null,
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
          <Button size="sm" onClick={() => void saveDraft()} disabled={!instructions.trim() || !role.trim() || !workflow.trim() || updateDraft.isPending}>保存草稿</Button>
        </div>
        <div className="space-y-5">
          <label className="block space-y-1.5 text-xs text-muted-foreground">Instructions<Textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} className="min-h-36 resize-y text-sm text-foreground" placeholder="描述智能体的职责、边界和执行要求" /></label>
          <div className="grid gap-4 lg:grid-cols-3">
            <label className="block space-y-1.5 text-xs text-muted-foreground">业务角色<Input value={role} onChange={(event) => setRole(event.target.value)} placeholder="sales_researcher" /></label>
            <label className="block space-y-1.5 text-xs text-muted-foreground">必需 Capability（逗号分隔）<Input value={requiredCapabilities} onChange={(event) => setRequiredCapabilities(event.target.value)} placeholder="streaming, browser" /></label>
            <label className="block space-y-1.5 text-xs text-muted-foreground">证据要求（逗号分隔）<Input value={evidenceRequirements} onChange={(event) => setEvidenceRequirements(event.target.value)} placeholder="citations, lineage" /></label>
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <label className="block space-y-1.5 text-xs text-muted-foreground">Input schema<Textarea value={inputSchema} onChange={(event) => setInputSchema(event.target.value)} spellCheck={false} className="min-h-64 resize-y font-mono text-xs text-foreground" /></label>
            <label className="block space-y-1.5 text-xs text-muted-foreground">Output schema<Textarea value={outputSchema} onChange={(event) => setOutputSchema(event.target.value)} spellCheck={false} className="min-h-64 resize-y font-mono text-xs text-foreground" /></label>
            <label className="block space-y-1.5 text-xs text-muted-foreground">State schema<Textarea value={stateSchema} onChange={(event) => setStateSchema(event.target.value)} spellCheck={false} className="min-h-64 resize-y font-mono text-xs text-foreground" /></label>
          </div>
          <details className="rounded-lg border border-white/[0.08] px-4 py-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">Runtime Binding 高级配置</summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-4">
              <label className="space-y-1.5 text-xs text-muted-foreground">Agent URL<Input type="url" value={agentUrl} onChange={(event) => setAgentUrl(event.target.value)} placeholder="https://agent.example.com" /></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">Runtime<select value={runtime} onChange={(event) => setRuntime(event.target.value as AgentRuntimeBindingV2['preferred_runtimes'][number])} className="h-9 w-full rounded-lg border bg-background px-3 text-sm text-foreground"><option value="miniflow">MiniFlow</option><option value="pi">Pi</option><option value="codex">Codex</option></select></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">Workflow<Input value={workflow} onChange={(event) => setWorkflow(event.target.value)} placeholder="default" /></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">深度执行超时（秒）<Input type="number" min={1} max={3600} value={dispatchTimeout} onChange={(event) => setDispatchTimeout(Number(event.target.value))} /><span className="block text-[11px] leading-4 text-muted-foreground">默认 30 分钟；本地 CLI 不暴露 5 小时额度，因此不伪造剩余额度。</span></label>
            </div>
          </details>
          <details className="rounded-lg border border-white/[0.08] px-4 py-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">Runtime / Provider / Model 高级配置</summary>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">节点和 Runtime 仅作为调度偏好；最终执行器按 Capability 匹配。Provider 凭据保留在 Fleet 节点，控制面只保存 auth profile 引用。</p>
            <div className="mt-4 grid gap-4 sm:grid-cols-4">
              <label className="space-y-1.5 text-xs text-muted-foreground">Fleet 节点偏好<select value={agentUrl} onChange={(event) => { const nextUrl = event.target.value; const node = runtimeNodes.find((candidate) => candidate.url === nextUrl); setAgentUrl(nextUrl); if (runtime && node && !Object.hasOwn(node.runtime_capabilities ?? {}, runtime)) setRuntime('') }} className="h-9 w-full rounded-lg border bg-background px-3 text-sm text-foreground"><option value="">无节点偏好</option>{runtimeNodes.map((node) => <option key={node.id} value={node.url}>{node.label}</option>)}</select></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">Runtime 偏好<select value={runtime} onChange={(event) => setRuntime(event.target.value)} className="h-9 w-full rounded-lg border bg-background px-3 text-sm text-foreground"><option value="">无 Runtime 偏好</option>{runtimeOptions.map((runtimeName) => <option key={runtimeName} value={runtimeName}>{runtimeName}</option>)}</select></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">Workflow<Input value={workflow} onChange={(event) => setWorkflow(event.target.value)} placeholder="operations-agent" /></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">深度执行超时（秒）<Input type="number" min={1} max={3600} value={dispatchTimeout} onChange={(event) => setDispatchTimeout(Number(event.target.value))} /></label>
            </div>
            <div className="mt-4 grid gap-4 sm:grid-cols-4">
              <label className="space-y-1.5 text-xs text-muted-foreground">Provider<Input value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="openrouter" /></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">Model<Input value={model} onChange={(event) => setModel(event.target.value)} placeholder="anthropic/claude-sonnet" /></label>
              <label className="space-y-1.5 text-xs text-muted-foreground">Auth profile 引用<Input value={authProfile} onChange={(event) => setAuthProfile(event.target.value)} placeholder="sales-production" /></label>
            </div>
            {!runtimeNodes.length ? <p className="mt-3 text-xs text-amber-300">没有已连接并报告 Capability manifest 的 Fleet 节点；草稿可以保存，但运行会按 Contract 要求失败关闭。</p> : null}
            <label className="mt-4 block space-y-1.5 text-xs text-muted-foreground">Task config（仅 timeout_seconds）<Textarea value={runtimeConfig} onChange={(event) => setRuntimeConfig(event.target.value)} spellCheck={false} className="min-h-24 resize-y font-mono text-xs text-foreground" /></label>
          </details>
        </div>
      </div>
      <aside className="min-h-0 overflow-y-auto border-l border-white/[0.08] bg-white/[0.015] p-5">
        <h3 className="text-xs font-medium text-muted-foreground">发布新版本</h3>
        <Textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="发布原因（必填）" className="mt-3 min-h-20 resize-y text-sm" />
        <Button className="mt-3 w-full" size="sm" onClick={() => void publish()} disabled={!reason.trim() || publishVersion.isPending}>发布 Contract</Button>
        <div className="mt-7 border-t border-white/[0.08] pt-5">
          <h3 className="text-xs font-medium text-muted-foreground">版本历史</h3>
          {versions.isLoading ? <div className="mt-3 text-xs text-muted-foreground">加载中…</div> : versions.isError ? <p className="mt-3 text-xs text-destructive">{(versions.error as Error)?.message}</p> : !versions.data?.length ? <p className="mt-3 text-xs text-muted-foreground">尚未发布版本</p> : <div className="mt-3 space-y-2">{versions.data.map((version) => <div key={version.version} className="rounded-lg border border-white/[0.08] p-3"><div className="flex items-center justify-between gap-2 text-xs"><span className="font-medium text-foreground">v{version.version}</span><Button size="xs" variant="ghost" onClick={() => setSelectedVersion(version.version)}>{selectedVersion === version.version ? '已选择' : '查看详情'}</Button></div><p className="mt-2 text-xs leading-5 text-muted-foreground">{version.reason}</p><p className="mt-1 text-[11px] text-muted-foreground">{new Date(version.created_at).toLocaleString()}</p></div>)}</div>}
          {selectedVersion !== null ? (
            <div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 p-3">
              <p className="text-xs font-medium text-foreground">v{selectedVersion} Contract 详情</p>
              {selectedVersionQuery.isLoading ? <p className="mt-2 text-xs text-muted-foreground">加载详情中…</p> : selectedVersionQuery.isError ? <p className="mt-2 text-xs text-destructive">{(selectedVersionQuery.error as Error)?.message}</p> : selectedVersionQuery.data ? <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-5 text-muted-foreground">{JSON.stringify(selectedVersionQuery.data, null, 2)}</pre> : null}
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  )
}

export default function OperationsAgentsPage() {
  const workspaces = useGovernedWorkspaces()
  const [workspaceId, setWorkspaceId] = useState<string | null>(null)
  const [view, setView] = useState<'automations' | 'agents'>('automations')
  const automations = useAutomations(workspaceId)
  const agents = useOperationsAgents(workspaceId)
  const teams = useOperationsAgentTeams(workspaceId)
  const activity = useOperationsAgentActivity(workspaceId)
  const installStarterPack = useInstallAutomationStarters()
  const createAutomation = useCreateAutomation()
  const patchAutomation = usePatchAutomation()
  const startAutomationRun = useStartAutomationRun()
  const startRunMutation = useStartOperationsAgentRun()
  const [open, setOpen] = useState(false)
  const [selectedAgent, setSelectedAgent] = useState<OperationsAgent | null>(null)
  const [agentDetailView, setAgentDetailView] = useState<'activity' | 'contract'>('activity')
  const [automationDraft, setAutomationDraft] = useState('')
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [precheck, setPrecheck] = useState('')
  const [automationAgentId, setAutomationAgentId] = useState('')
  const [projectPath, setProjectPath] = useState('')
  const [branch, setBranch] = useState('main')
  const [scheduleKind, setScheduleKind] = useState('weekdays')
  const [weeklyDay, setWeeklyDay] = useState('monday')
  const [time, setTime] = useState('09:00')
  const [sessionMode, setSessionMode] = useState<'fresh' | 'reuse'>('fresh')
  const [approvalMode, setApprovalMode] = useState<OperationsAgentMode>('suggest_changes')
  const [runTargetId, setRunTargetId] = useState('')
  const [runInput, setRunInput] = useState('{}')
  const [runState, setRunState] = useState('{}')
  const [runTargetType, setRunTargetType] = useState('manual')
  const latestRun = useMemo(() => new Map(activity.data?.map((run) => [run.operations_agent_id, run]) ?? []), [activity.data])

  useEffect(() => {
    if (!workspaceId && workspaces.data?.length) {
      setWorkspaceId(workspaces.data[0].id)
    }
  }, [workspaceId, workspaces.data])

  function startCreate(preset?: AgentStarterInput) {
    setName(preset?.name ?? '')
    setPrompt(preset?.prompt ?? '')
    setExecutor(preset?.executor ?? 'codex')
    if (preset) {
      const [kind, qualifier, weeklyTime] = preset.schedule.split('@')
      setScheduleKind(kind)
      if (kind === 'weekly') {
        setWeeklyDay(qualifier || 'monday')
        setTime(weeklyTime || '09:00')
      } else if (qualifier) {
        setTime(qualifier)
      }
    }
    setOpen(true)
  }

  async function installAgentStarters() {
    if (!workspaceId || installStarterPack.isPending) return
    try {
      const result = await installStarterPack.mutateAsync({ workspaceId })
      toast.success(`已支起 ${result.created_count} 个 Agent Starter，跳过 ${result.skipped_count} 个`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Agent Starter 创建失败')
    }
  }

  function configureDraft() {
    const draft = automationDraft.trim()
    if (!draft) return
    setName('')
    setPrompt(draft)
    setOpen(true)
  }

  function startAgentCreate(starter?: AgentStarterInput) {
    setAgentName(starter?.name ?? '')
    setAgentDescription(starter?.prompt ?? '')
    setOwningTeamId(teams.data?.length === 1 ? teams.data[0].id : '')
    setAgentCreateOpen(true)
  }

  async function submitAgentCreate() {
    if (!workspaceId || !agentName.trim() || !owningTeamId) return
    try {
      const agent = await createOperationsAgent.mutateAsync({
        workspaceId,
        data: {
          name: agentName.trim(),
          description: agentDescription.trim() || null,
          owning_team_id: owningTeamId,
        },
      })
      setAgentCreateOpen(false)
      setView('agents')
      setSelectedAgent(agent)
      setAgentDetailView('contract')
      toast.success('Operations Agent 已创建；请确认 Runtime Contract 后发布')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Operations Agent 创建失败')
    }
  }

  async function submitCreate() {
    if (!workspaceId) return
    const boundAgent = compatibleAgents.find((agent) => agent.id === automationAgentId)
    if (!boundAgent?.current_published_version) return
    try {
      const schedule = scheduleKind === 'on_anomaly'
        ? 'on_anomaly'
        : scheduleKind === 'weekly'
          ? `weekly@${weeklyDay}@${time}`
          : `${scheduleKind}@${time}`
      await createAutomation.mutateAsync({ workspaceId, data: {
        operations_agent_id: boundAgent.id,
        operations_agent_version: boundAgent.current_published_version,
        name: name.trim(), prompt: prompt.trim(), precheck: precheck.trim() || null,
        executor, schedule, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        session_mode: sessionMode, approval_mode: approvalMode,
        project: { path: projectPath.trim() || null, branch: branch.trim() || null }, enabled: true,
      } })
      setOpen(false)
      toast.success('自动化已创建并绑定已发布智能体')
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

  function openAutomationRun(automation: Automation) {
    setAutomationToRun(automation)
  }

  async function runAutomationNow() {
    if (!workspaceId || !automationToRun) return
    try {
      const run = await startAutomationRun.mutateAsync({
        workspaceId,
        automationId: automationToRun.id,
      })
      const agent = agents.data?.find(
        (candidate) => candidate.id === automationToRun.operations_agent_id,
      )
      setAutomationToRun(null)
      if (agent) {
        setSelectedAgent(agent)
        setAgentDetailView('activity')
        setView('agents')
      }
      toast.success(`Automation Run ${run.id} 已提交`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Automation Run 启动失败')
    }
  }

  function openAutomationBinding(automation: Automation) {
    setAutomationToBind(automation)
    setBindingAgentId(automation.operations_agent_id ?? runnableAgents[0]?.id ?? '')
  }

  async function bindAutomation() {
    if (!workspaceId || !automationToBind) return
    const agent = runnableAgents.find((candidate) => candidate.id === bindingAgentId)
    if (!agent?.current_published_version) return
    try {
      await patchAutomation.mutateAsync({
        workspaceId,
        automationId: automationToBind.id,
        data: {
          operations_agent_id: agent.id,
          operations_agent_version: agent.current_published_version,
          approval_mode: agent.current_profile.mode,
          executor: 'operations-agent',
          enabled: true,
        },
      })
      setAutomationToBind(null)
      toast.success(`已绑定 ${agent.name} v${agent.current_published_version}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '智能体绑定失败')
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

        <div className="mt-10 flex items-center gap-1 border-b border-white/[0.08]" role="tablist" aria-label="自动化与智能体视图">
          <button type="button" role="tab" aria-selected={view === 'automations'} onClick={() => setView('automations')} className={cn('border-b-2 px-4 py-3 text-sm transition-colors', view === 'automations' ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}><CalendarClock className="mr-2 inline size-4" />自动化</button>
          <button type="button" role="tab" aria-selected={view === 'agents'} onClick={() => setView('agents')} className={cn('border-b-2 px-4 py-3 text-sm transition-colors', view === 'agents' ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}><Bot className="mr-2 inline size-4" />智能体</button>
        </div>

        <section className="mt-8" aria-labelledby="agent-starters-title">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">SmoothUI / Agent starters</p>
              <h2 id="agent-starters-title" className="mt-1 text-xl font-medium tracking-[-0.015em]">先把这三个 Agent 支起来</h2>
              <p className="mt-1 text-sm text-muted-foreground">三套可直接创建的自动化模板；创建后会进入我的自动化并按日程执行。</p>
            </div>
            <Button size="sm" onClick={() => void installAgentStarters()} disabled={installStarterPack.isPending}>
              {installStarterPack.isPending ? '正在安装…' : '一键安装三个 Agent'}
            </Button>
          </div>
          <div className="grid gap-3 lg:grid-cols-3">
            {AGENT_STARTERS.map((starter) => (
              <SwitchboardCard
                key={starter.name}
                title={starter.name}
                subtitle={`${starter.subtitle} · ${executorMeta(starter.executor).name}`}
                columns={18}
                rows={5}
                gridPattern={[...starter.pattern]}
                className="h-[260px] p-4"
                onButtonClick={() => startCreate(starter)}
              />
            ))}
          </div>
        </section>

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
                <div className="mt-7 space-y-1">{SUGGESTIONS.map((item) => { const Icon = item.icon; return <button key={item.name} type="button" onClick={() => startCreate(item)} className="group flex w-full items-start gap-4 rounded-xl px-3 py-3 text-left transition-colors hover:bg-white/[0.04]"><Icon className={`mt-0.5 size-5 ${item.color}`} /><span className="flex-1"><span className="block text-sm font-medium">{item.name} <span className="ml-2 font-normal text-muted-foreground">{automationScheduleText(item.schedule)}</span></span><span className="mt-1 block text-sm text-muted-foreground">{item.prompt}</span></span><Plus className="mt-2 size-4 opacity-0 transition-opacity group-hover:opacity-100" /></button> })}</div>
              </div>
            </section>
            <section className="mt-12">
              <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-medium text-muted-foreground">我的自动化</h2><Button variant="ghost" size="sm" onClick={() => startCreate()}><Plus />手动配置</Button></div>
              {automations.isLoading ? (
                <LoadingState rows={3} />
              ) : automations.isError ? (
                <ErrorState message={(automations.error as Error)?.message} hint={BACKEND_HINT} />
              ) : !automations.data?.length ? (
                <EmptyState title="还没有自动化" description="使用模板或创建一个新的定时任务。" />
              ) : (
                <div className="divide-y divide-white/[0.06]">
                  {automations.data.map((automation) => {
                    const meta = executorMeta(automation.executor)
                    const Icon = meta.icon
                    const boundAgent = agents.data?.find(
                      (agent) => agent.id === automation.operations_agent_id,
                    )
                    return (
                      <div key={automation.id} className="flex items-start gap-4 rounded-lg px-3 py-4 hover:bg-white/[0.03]">
                        <span className={cn('mt-0.5 flex size-7 items-center justify-center rounded-md bg-white/[0.05]', meta.color)}><Icon className="size-4" /></span>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-3">
                            <span className="text-sm font-medium">{automation.name}</span>
                            <span className={cn('rounded-full px-2 py-0.5 text-[11px]', automation.enabled ? 'bg-white/[0.06] text-muted-foreground' : 'bg-amber-400/10 text-amber-300')}>{automation.enabled ? '已启用' : '已暂停'}</span>
                            <span className="text-xs text-muted-foreground">{automationScheduleText(automation.schedule)}</span>
                            <span className="text-xs text-muted-foreground">{meta.name}</span>
                          </div>
                          <p className="mt-1 truncate text-sm text-muted-foreground">{automation.prompt}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{boundAgent ? `绑定 ${boundAgent.name} · Agent v${automation.operations_agent_version} · Automation r${automation.revision}` : '尚未绑定已发布 Operations Agent；调度保持暂停'}</p>
                        </div>
                        <div className="flex items-center gap-1">
                          <Button size="sm" variant="ghost" onClick={() => openAutomationBinding(automation)}>{boundAgent ? '更换绑定' : '绑定智能体'}</Button>
                          <Button size="sm" variant="outline" disabled={!automation.enabled || !boundAgent} onClick={() => openAutomationRun(automation)}><Play />立即运行</Button>
                          <Button size="icon-sm" variant="ghost" disabled={!automation.enabled && !boundAgent} aria-label={automation.enabled ? `暂停 ${automation.name}` : `恢复 ${automation.name}`} onClick={() => void toggleAutomation(automation)}>{automation.enabled ? <Pause /> : <Play />}</Button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </section>
          </div>
        ) : (
          <section className="mt-10">
            <div className="mb-7 flex items-start justify-between gap-4"><div><h2 className="text-xl font-medium tracking-[-0.015em]">智能体正在做什么</h2><p className="mt-1 text-sm text-muted-foreground">状态每 5 秒更新；需要你决定的动作会进入 Inbox。</p></div><Button onClick={() => startAgentCreate()}><Plus />添加智能体</Button></div>
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
            <label className="space-y-1.5 text-xs text-muted-foreground">绑定已发布智能体<select value={automationAgentId} onChange={(event) => setAutomationAgentId(event.target.value)} className="h-10 w-full rounded-lg border bg-background px-3 text-sm text-foreground"><option value="">选择与审批模式兼容的智能体</option>{compatibleAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · v{agent.current_published_version} · {APPROVALS.find((item) => item.id === agent.current_profile.mode)?.label}</option>)}</select></label>
            <div className="grid gap-4 sm:grid-cols-2"><label className="space-y-1.5 text-xs text-muted-foreground">项目路径<Input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="/workspace/project" /></label><label className="space-y-1.5 text-xs text-muted-foreground">基础分支<Input value={branch} onChange={(event) => setBranch(event.target.value)} /></label></div>
            <div className="grid gap-4 sm:grid-cols-4"><label className="space-y-1.5 text-xs text-muted-foreground">日程<select value={scheduleKind} onChange={(event) => setScheduleKind(event.target.value)} className="h-9 w-full rounded-lg border bg-background px-3 text-sm"><option value="hourly">每小时</option><option value="daily">每天</option><option value="weekdays">工作日</option><option value="weekly">每周</option><option value="on_anomaly">检测到异常时</option></select></label>{scheduleKind === 'weekly' ? <label className="space-y-1.5 text-xs text-muted-foreground">星期<select value={weeklyDay} onChange={(event) => setWeeklyDay(event.target.value)} className="h-9 w-full rounded-lg border bg-background px-3 text-sm"><option value="monday">周一</option><option value="tuesday">周二</option><option value="wednesday">周三</option><option value="thursday">周四</option><option value="friday">周五</option><option value="saturday">周六</option><option value="sunday">周日</option></select></label> : null}<label className="space-y-1.5 text-xs text-muted-foreground">时间<Input type="time" value={time} onChange={(event) => setTime(event.target.value)} disabled={scheduleKind === 'hourly' || scheduleKind === 'on_anomaly'} /></label><label className="space-y-1.5 text-xs text-muted-foreground">会话<select value={sessionMode} onChange={(event) => setSessionMode(event.target.value as 'fresh' | 'reuse')} className="h-9 w-full rounded-lg border bg-background px-3 text-sm"><option value="fresh">每次新会话</option><option value="reuse">重复利用会话</option></select></label></div>
            <fieldset><legend className="mb-2 text-xs text-muted-foreground">审批方式</legend><div className="grid gap-2 sm:grid-cols-3">{APPROVALS.map((item) => <button key={item.id} type="button" onClick={() => setApprovalMode(item.id)} className={cn('rounded-lg border p-3 text-left', approvalMode === item.id ? 'border-foreground bg-white/[0.06]' : 'border-white/[0.08]')}><span className="block text-sm font-medium">{item.label}</span><span className="mt-1 block text-xs text-muted-foreground">{item.detail}</span></button>)}</div></fieldset>
            <details className="rounded-lg border border-white/[0.08] px-4 py-3"><summary className="cursor-pointer text-sm text-muted-foreground">高级设置</summary><label className="mt-4 block space-y-1.5 text-xs text-muted-foreground"><Terminal className="mr-1 inline size-3" />预检查<Input value={precheck} onChange={(event) => setPrecheck(event.target.value)} placeholder="可选：运行前检查命令" className="font-mono" /></label></details>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>取消</Button><Button onClick={() => void submitCreate()} disabled={!name.trim() || !prompt.trim() || !automationAgentId || createAutomation.isPending}><Plus />创建并启用</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={agentCreateOpen} onOpenChange={setAgentCreateOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>添加 Operations Agent</DialogTitle>
            <DialogDescription>创建 Observe Only 身份后，继续配置已连接 Fleet 节点的 Runtime Contract 并发布。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <Input value={agentName} onChange={(event) => setAgentName(event.target.value)} placeholder="智能体名称" autoFocus />
            <Textarea value={agentDescription} onChange={(event) => setAgentDescription(event.target.value)} placeholder="职责与只读边界" className="min-h-32" />
            <label className="space-y-1.5 text-xs text-muted-foreground">所属 Team<select value={owningTeamId} onChange={(event) => setOwningTeamId(event.target.value)} className="h-10 w-full rounded-lg border bg-background px-3 text-sm text-foreground"><option value="">选择 Team</option>{(teams.data ?? []).map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAgentCreateOpen(false)}>取消</Button>
            <Button onClick={() => void submitAgentCreate()} disabled={!agentName.trim() || !owningTeamId || createOperationsAgent.isPending}><Plus />创建并配置</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(automationToRun)} onOpenChange={(nextOpen) => { if (!nextOpen) setAutomationToRun(null) }}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>立即运行自动化</DialogTitle>
            <DialogDescription>{automationToRun?.name} 将使用已持久化的 Agent/version 绑定执行；Run 会固定 Automation revision 与完整快照。</DialogDescription>
          </DialogHeader>
          <p className="rounded-lg border border-white/[0.08] p-3 text-sm text-muted-foreground">
            Agent {automationToRun?.operations_agent_id} · v{automationToRun?.operations_agent_version} · Automation r{automationToRun?.revision}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAutomationToRun(null)}>取消</Button>
            <Button onClick={() => void runAutomationNow()} disabled={startAutomationRun.isPending}><Play />提交 Run</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(automationToBind)} onOpenChange={(nextOpen) => { if (!nextOpen) setAutomationToBind(null) }}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>绑定 Operations Agent</DialogTitle>
            <DialogDescription>绑定会固定当前已发布 Agent 版本；Low-Risk Automatic Agent 不可用于 Automation。</DialogDescription>
          </DialogHeader>
          <label className="grid gap-2 text-sm text-muted-foreground">已发布智能体
            <select value={bindingAgentId} onChange={(event) => setBindingAgentId(event.target.value)} className="h-10 rounded-lg border bg-background px-3 text-foreground">
              <option value="">选择智能体</option>
              {runnableAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · v{agent.current_published_version} · {APPROVALS.find((item) => item.id === agent.current_profile.mode)?.label}</option>)}
            </select>
          </label>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAutomationToBind(null)}>取消</Button>
            <Button onClick={() => void bindAutomation()} disabled={!bindingAgentId || patchAutomation.isPending}>绑定并启用</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(selectedAgent)} onOpenChange={(nextOpen) => { if (!nextOpen) setSelectedAgent(null) }}>
        <DialogContent className="flex h-[82vh] max-h-[820px] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl">
          {selectedAgent ? <>
            <DialogHeader className="border-b border-white/[0.08] px-6 py-4">
              <div className="flex items-center gap-3 pr-8"><AgentAvatar seed={selectedAgent.id} size={36} /><div className="min-w-0"><DialogTitle className="flex items-center gap-2 text-base"><span className="truncate">{selectedAgent.name}</span><span className={cn('text-xs font-normal', selectedAgent.disabled ? 'text-amber-300' : latestRun.get(selectedAgent.id)?.status === 'running' ? 'text-emerald-400' : 'text-muted-foreground')}>{selectedAgent.disabled ? '已停用' : latestRun.get(selectedAgent.id)?.status === 'running' ? '工作中' : '空闲'}</span></DialogTitle><DialogDescription className="mt-0.5">CLI Agent 会话 · {APPROVALS.find((item) => item.id === selectedAgent.current_profile.mode)?.label ?? selectedAgent.current_profile.mode}</DialogDescription></div></div>
              <div className="mt-3 flex gap-1" role="tablist" aria-label="智能体详情"><button type="button" role="tab" aria-selected={agentDetailView === 'activity'} onClick={() => setAgentDetailView('activity')} className={cn('rounded-md px-3 py-1.5 text-xs', agentDetailView === 'activity' ? 'bg-white/[0.08] text-foreground' : 'text-muted-foreground hover:text-foreground')}>活动</button><button type="button" role="tab" aria-selected={agentDetailView === 'contract'} onClick={() => setAgentDetailView('contract')} className={cn('rounded-md px-3 py-1.5 text-xs', agentDetailView === 'contract' ? 'bg-white/[0.08] text-foreground' : 'text-muted-foreground hover:text-foreground')}>Contract</button></div>
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
              <aside className="border-l border-white/[0.08] bg-white/[0.015] p-5 text-xs"><div className="text-muted-foreground">职责</div><p className="mt-2 text-sm leading-6 text-foreground">{selectedAgent.description || '尚未填写职责说明'}</p><div className="mt-6 text-muted-foreground">权限模式</div><p className="mt-2 text-sm text-foreground">{APPROVALS.find((item) => item.id === selectedAgent.current_profile.mode)?.label}</p><p className="mt-1 leading-5 text-muted-foreground">Profile v{selectedAgent.current_profile.version} · {selectedAgent.current_profile.reason}</p><Button variant="outline" size="sm" className="mt-6 w-full" onClick={() => setAgentDetailView('contract')}>配置智能体</Button></aside>
            </div> : workspaceId ? <ContractEditor workspaceId={workspaceId} agent={selectedAgent} /> : null}
          </> : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}

'use client'

import { ArrowLeft, ArrowRight, Bot, Braces, CheckCircle2, KeyRound, ShieldCheck, Waypoints } from 'lucide-react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { use, useEffect, useMemo, useState } from 'react'
import { EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { PageContainer } from '@/components/shell/page-container'
import ButtonCopy from '@/components/smoothui/button-copy'
import { ProjectNavigation } from '@/components/studio/project-navigation'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useProjectWorkflows, useWorkspaceProjects } from '@/lib/api/hooks'
import { cn } from '@/lib/utils'

export default function ProjectApiAccessPage({
  params,
}: {
  params: Promise<{ projectId: string }>
}) {
  const { projectId } = use(params)
  const searchParams = useSearchParams()
  const workspaceId = searchParams.get('workspace')
  const preferredWorkflowId = searchParams.get('workflow')
  const projectsQuery = useWorkspaceProjects(workspaceId)
  const workflowsQuery = useProjectWorkflows(workspaceId, projectId)
  const project = projectsQuery.data?.find((candidate) => candidate.id === projectId)
  const workflows = workflowsQuery.data ?? []
  const initialWorkflowId = preferredWorkflowId ?? project?.primary_workflow_id ?? workflows[0]?.id ?? ''
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('')
  const workflowId = workflows.some((workflow) => workflow.id === selectedWorkflowId)
    ? selectedWorkflowId
    : initialWorkflowId
  const workflow = workflows.find((candidate) => candidate.id === workflowId)
  const [apiOrigin, setApiOrigin] = useState('')
  useEffect(() => setApiOrigin(window.location.origin), [])
  const endpoint = workspaceId && workflowId
    ? `/api/v1/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs`
    : ''
  const absoluteEndpoint = apiOrigin && endpoint ? `${apiOrigin}${endpoint}` : endpoint
  const mcpEndpoint = apiOrigin ? `${apiOrigin}/mcp` : '/mcp'
  const mcpConfig = useMemo(() => JSON.stringify({
    mcpServers: {
      'opencli-admin': {
        type: 'http',
        url: mcpEndpoint,
        headers: {
          Authorization: 'Bearer <API_AUTH_TOKEN>',
        },
      },
    },
  }, null, 2), [mcpEndpoint])
  const traceTemplate = workspaceId && workflowId
    ? `/api/v1/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs/{run_id}/trace`
    : ''
  const requestBody = useMemo(() => JSON.stringify({
    inputs: { topic: 'OpenCLI ecosystem' },
    response_mode: 'async',
    user: 'server-worker',
  }, null, 2), [])
  const curl = [
    `curl -X POST "${absoluteEndpoint}"`,
    '  -H "Authorization: Bearer $API_AUTH_TOKEN"',
    '  -H "Content-Type: application/json"',
    '  -H "Idempotency-Key: project-job-001"',
    `  -d '${requestBody.replaceAll('\n', '\n  ')}'`,
  ].join(' \\\n')
  const loading = projectsQuery.isLoading || workflowsQuery.isLoading
  const error = projectsQuery.error || workflowsQuery.error
  const overviewHref = workspaceId ? `/studio/projects/${projectId}?workspace=${workspaceId}` : '/studio'
  const operationsHref = workspaceId
    ? `/studio/projects/${projectId}/operations?workspace=${workspaceId}${workflowId ? `&workflow=${workflowId}` : ''}`
    : null

  return (
    <PageContainer
      eyebrow="Published API & MCP"
      title={project ? `${project.name} · API / MCP` : '项目 API / MCP'}
      description="REST 与 MCP 共用已发布版本、运行日志和 Trace；服务端或 Agent 都从这一页接入。"
      className="max-w-none"
      actions={<Link href={overviewHref} className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'min-h-11')}><ArrowLeft className="size-4" />返回项目</Link>}
    >
      <div className="border-b pb-3">
        <ProjectNavigation active="apiAccess" workspaceId={workspaceId} projectId={projectId} workflowId={workflowId} />
      </div>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]" aria-labelledby="mcp-title">
        <Card className="overflow-hidden border-primary/25">
          <CardHeader className="border-b bg-primary/[0.035]">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="eyebrow-mono">MCP 2026-07-28</p>
                <CardTitle id="mcp-title" className="mt-1 flex items-center gap-2 text-base"><Waypoints className="size-4 text-primary" />内置 Streamable HTTP</CardTitle>
              </div>
              <div className="flex gap-2">
                <Badge variant="secondary">Stateless</Badge>
                <Badge variant="secondary">server/discover</Badge>
                <Badge variant="secondary">JSON Schema</Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4 p-5">
            <div className="flex min-w-0 items-center gap-3 rounded-lg border bg-muted/25 px-4 py-3">
              <span className="font-mono text-xs font-semibold text-primary">MCP</span>
              <code className="min-w-0 flex-1 truncate text-xs">{mcpEndpoint}</code>
              <ButtonCopy text={mcpEndpoint} label="复制 MCP 地址" disabled={!apiOrigin} iconOnly variant="ghost" size="icon-sm" />
            </div>
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground">远程客户端直接连接同源端点；协议版本、能力和缓存信息由发现请求协商。</p>
              <ButtonCopy text={mcpConfig} label="复制配置" disabled={!apiOrigin} variant="outline" size="sm" className="min-h-11 md:min-h-7" />
            </div>
            <pre className="max-h-72 overflow-auto rounded-lg border bg-[#090b10] p-4 font-mono text-xs leading-6 text-zinc-200">{mcpConfig}</pre>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Bot className="size-4" />项目级工具已接入</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <McpTool name="list_project_workflows" detail="读取发布状态与版本" />
            <McpTool name="run_published_workflow" detail="显式 Idempotency Key" />
            <McpTool name="get_project_runtime_summary" detail="运行聚合与近期活动" />
            <McpTool name="list_project_runtime_logs" detail="搜索、状态与分页" />
            <McpTool name="get_project_runtime_trace" detail="Projection、Checkpoint、Events" />
            <div className="rounded-lg border border-success/25 bg-success/5 p-3 text-xs leading-5 text-muted-foreground">
              这些工具复用同一条发布版本、运行日志和 Trace 链路，不维护第二套执行状态。
            </div>
          </CardContent>
        </Card>
      </section>

      {loading ? <LoadingState rows={6} /> : error ? (
        <ErrorState message={error instanceof Error ? error.message : 'API 上下文加载失败'} hint="确认后端、工作区和项目上下文可用。" />
      ) : !project ? (
        <EmptyState title="找不到项目" description="返回 Studio 重新选择工作区和项目。" />
      ) : workflows.length === 0 ? (
        <EmptyState title="项目还没有工作流" description="先创建工作流并发布一个版本，API 才有稳定的执行目标。" />
      ) : (
        <>
          <section className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_19rem]" aria-label="API 调用上下文">
            <Card>
              <CardContent className="grid gap-4 p-5 md:grid-cols-[14rem_minmax(0,1fr)] md:items-end">
                <div>
                  <div className="mb-2 text-xs font-medium">调用工作流</div>
                  <Select value={workflowId} onValueChange={(value) => setSelectedWorkflowId(value ?? '')}>
                    <SelectTrigger className="w-full" aria-label="选择 API 工作流"><SelectValue>{workflow?.name ?? '选择工作流'}</SelectValue></SelectTrigger>
                    <SelectContent>
                      {workflows.map((candidate) => (
                        <SelectItem key={candidate.id} value={candidate.id}>{candidate.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex min-w-0 items-center gap-3 rounded-lg border bg-muted/25 px-4 py-3">
                  <span className="font-mono text-xs font-semibold text-primary">POST</span>
                  <code className="min-w-0 flex-1 truncate text-xs">{absoluteEndpoint}</code>
                  <ButtonCopy text={absoluteEndpoint} label="复制 API 地址" disabled={!apiOrigin || !endpoint} iconOnly variant="ghost" size="icon-sm" />
                </div>
              </CardContent>
            </Card>
            <Card className={workflow?.current_published_version ? 'border-success/30' : 'border-warning/40'}>
              <CardContent className="flex h-full items-center gap-3 p-5">
                {workflow?.current_published_version ? <CheckCircle2 className="size-5 text-success" /> : <Braces className="size-5 text-warning" />}
                <div>
                  <div className="text-sm font-medium">{workflow?.current_published_version ? `Published v${workflow.current_published_version}` : '尚未发布'}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{workflow?.current_published_version ? 'API 固定读取该发布版本' : '调用会返回 409，不会执行 Draft'}</div>
                </div>
              </CardContent>
            </Card>
          </section>

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between gap-3">
                  <div><p className="eyebrow-mono">Request</p><CardTitle className="mt-1 text-base">服务端调用</CardTitle></div>
                  <ButtonCopy text={curl} label="复制 cURL" disabled={!apiOrigin || !endpoint} variant="outline" size="sm" className="min-h-11 md:min-h-7" />
                </div>
              </CardHeader>
              <CardContent>
                <pre className="max-h-[30rem] overflow-auto rounded-lg border bg-[#090b10] p-4 font-mono text-xs leading-6 text-zinc-200">{curl}</pre>
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card>
                <CardHeader><CardTitle className="flex items-center gap-2 text-base"><KeyRound className="size-4" />认证边界</CardTitle></CardHeader>
                <CardContent className="space-y-3 text-sm text-muted-foreground">
                  <p>使用部署级 <code className="text-foreground">API_AUTH_TOKEN</code>；HTTP 支持 Bearer，也支持在已有 OIDC 身份时使用 <code className="text-foreground">X-API-Token</code>。</p>
                  <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs leading-5">
                    Token 只放在后端服务或 Worker 环境变量中，不写入浏览器、移动端或公开仓库。
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="size-4" />运行契约</CardTitle></CardHeader>
                <CardContent className="space-y-3 text-xs text-muted-foreground">
                  <Contract label="inputs" value="业务输入对象；调用方不能提交或替换 WorkflowProject" />
                  <Contract label="user" value="服务端调用者标识，进入运行来源审计" />
                  <Contract label="Idempotency-Key" value="同一发布版本内安全重试并复用 run_id" />
                  <Contract label="response_mode" value="当前真实链路固定为 async；用 run_id 查询 Trace" />
                </CardContent>
              </Card>
            </div>
          </section>

          <section className="rounded-xl border bg-card p-5" aria-labelledby="lifecycle-title">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><p className="eyebrow-mono">Observable lifecycle</p><h2 id="lifecycle-title" className="mt-1 text-base font-semibold">请求进入同一条运行与 Trace 链路</h2></div>
              {operationsHref ? <Link href={operationsHref} className={buttonVariants({ variant: 'outline', size: 'sm' })}>打开日志监测<ArrowRight className="size-4" /></Link> : null}
            </div>
            <div className="mt-5 grid gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-center">
              <LifecycleStep index="01" title="POST inputs" detail="校验项目、工作流和当前发布版本" />
              <ArrowRight className="mx-auto hidden size-4 text-muted-foreground md:block" />
              <LifecycleStep index="02" title="202 run_id" detail="持久化版本绑定、请求、状态与事件" />
              <ArrowRight className="mx-auto hidden size-4 text-muted-foreground md:block" />
              <LifecycleStep index="03" title="Trace" detail={traceTemplate} mono />
            </div>
          </section>
        </>
      )}
    </PageContainer>
  )
}

function Contract({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border p-3"><Badge variant="secondary" className="font-mono">{label}</Badge><p className="mt-2 leading-5">{value}</p></div>
}

function McpTool({ name, detail }: { name: string; detail: string }) {
  return <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5"><code className="min-w-0 truncate text-xs font-medium text-foreground">{name}</code><span className="shrink-0 text-[11px] text-muted-foreground">{detail}</span></div>
}

function LifecycleStep({ index, title, detail, mono = false }: { index: string; title: string; detail: string; mono?: boolean }) {
  return <div className="min-w-0 rounded-lg border p-4"><div className="font-mono text-[10px] text-primary">{index}</div><div className="mt-2 text-sm font-medium">{title}</div><div className={cn('mt-1 break-all text-xs leading-5 text-muted-foreground', mono && 'font-mono')}>{detail}</div></div>
}

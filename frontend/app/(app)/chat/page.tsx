'use client'

import Link from 'next/link'
import { ArrowRight, FolderOpen, MessageSquare, Settings2 } from 'lucide-react'
import { useSearchParams } from 'next/navigation'
import { useCallback, useState } from 'react'

import { ProjectArtifactsPanel } from '@/components/artifacts/project-artifacts-panel'
import { AgentConversationSurface } from '@/components/shell/agent-conversation-surface'
import { PageContainer } from '@/components/shell/page-container'
import { Button } from '@/components/ui/button'
import type { AgentConversation, AgentConversationDetail } from '@/lib/api/agent-conversations'

/** The dock and this page deliberately share one durable conversation store. */
export default function ChatPage() {
  const searchParams = useSearchParams()
  const workspaceId = searchParams.get('workspace')
  const projectId = searchParams.get('project')
  const workflowId = searchParams.get('workflow')
  const runId = searchParams.get('run')
  const [activeConversation, setActiveConversation] = useState<AgentConversationDetail | AgentConversation | null>(null)
  const activeContext = activeConversation?.context_binding
  // A durable chat session is stored in governed Workspace scope; project
  // artifacts must instead use the server-stamped Studio bridge scope.
  const resultWorkspaceId = activeConversation
    ? activeContext?.studio_workspace_id ?? activeConversation.workspace_id
    : workspaceId
  const resultProjectId = activeConversation ? activeContext?.project_id ?? null : projectId
  const resultWorkflowId = activeConversation ? activeContext?.workflow_id ?? null : workflowId
  const resultRunId = activeConversation ? activeContext?.run_id ?? null : runId
  const onActiveConversationChange = useCallback((conversation: AgentConversationDetail | AgentConversation | null) => {
    setActiveConversation(conversation)
  }, [])
  const studioHref = resultWorkspaceId && resultProjectId
    ? `/studio/projects/${encodeURIComponent(resultProjectId)}?workspace=${encodeURIComponent(resultWorkspaceId)}`
    : resultWorkspaceId ? `/studio?workspace=${encodeURIComponent(resultWorkspaceId)}` : '/studio'

  return (
    <PageContainer
      title="Ask Alice"
      eyebrow="主对话工作区"
      description="在已授权 Workspace 中继续会话、审阅工具结果并确认变更。"
      actions={(
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/providers" />}>
            <Settings2 aria-hidden />
            模型与连接
          </Button>
          <Button variant="outline" size="sm" nativeButton={false} render={<Link href={studioHref} />}>
            <FolderOpen aria-hidden />
            {resultProjectId ? '打开项目' : '进入 Studio'}
          </Button>
          <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/dashboard" />}>
            返回概览
            <ArrowRight aria-hidden />
          </Button>
        </div>
      )}
      className="max-w-[1800px]"
    >
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,380px)]">
        <section aria-labelledby="chat-main-title" className="min-w-0">
          <h2 id="chat-main-title" className="sr-only">Ask Alice 对话与执行工作区</h2>
          <AgentConversationSurface presentation="page" onActiveConversationChange={onActiveConversationChange} />
        </section>

        <aside className="min-w-0" aria-label="项目结果与文件">
          {resultWorkspaceId && resultProjectId ? (
            <ProjectArtifactsPanel workspaceId={resultWorkspaceId} projectId={resultProjectId} workflowId={resultWorkflowId} runId={resultRunId} />
          ) : (
            <section className="rounded-xl border bg-card p-5 shadow-sm">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <FolderOpen className="size-4 text-primary" aria-hidden />
                项目结果与文件
              </div>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                从 Studio 的项目、工作流或运行进入 Ask Alice 后，这里会显示该项目已授权的产物和报告预览。
              </p>
              <Button className="mt-4" variant="outline" size="sm" nativeButton={false} render={<Link href={studioHref} />}>
                <MessageSquare aria-hidden />
                选择项目并开始
              </Button>
            </section>
          )}
        </aside>
      </div>
    </PageContainer>
  )
}

'use client'

import Link from 'next/link'
import { ArrowRight, MessageSquare } from 'lucide-react'

import { AgentToolReference } from '@/components/agent/agent-tool-reference'
import { AgentConversationSurface } from '@/components/shell/agent-conversation-surface'
import { PageContainer } from '@/components/shell/page-container'
import { Button } from '@/components/ui/button'

export default function ChatPage() {
  return (
    <PageContainer
      title="Ask Alice"
      eyebrow="主对话工作区"
      description="在当前授权 Workspace 中查询状态、组织项目和确认变更。所有写入都会先展示提案。"
      actions={(
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/dashboard" />}>
          返回概览
          <ArrowRight aria-hidden />
        </Button>
      )}
      className="max-w-[1600px]"
    >
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(320px,390px)]">
        <section aria-labelledby="chat-main-title" className="min-w-0">
          <h2 id="chat-main-title" className="sr-only">Ask Alice 主对话</h2>
          <AgentConversationSurface presentation="page" />
        </section>
        <aside className="min-w-0" aria-label="当前工具与能力">
          <AgentToolReference />
        </aside>
      </div>
      <div className="flex items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
        <MessageSquare className="size-3.5 shrink-0" aria-hidden />
        <span>会话会按 Workspace 保存；从项目、运行或报告进入时，会继续显示对应上下文。</span>
      </div>
    </PageContainer>
  )
}

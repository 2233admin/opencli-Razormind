'use client'

import { LoaderCircle, MessageCircle, Send } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useAgentConversation, useSendAgentConversationMessage } from '@/lib/api/hooks'
import type { AgentConversationContext, AgentConversationTurn } from '@/lib/api/agent-conversations'

type InboxConversationThreadProps = {
  conversationId: string
  context: AgentConversationContext
}

function turnReply(turn: AgentConversationTurn) {
  if (turn.status === 'failed') return turn.error_message || '这次处理没有完成。'
  if (turn.response?.type === 'proposal') return turn.response.proposal?.summary || 'Agent 提交了一项待确认的提案。'
  return turn.response?.content || (turn.status === 'running' ? '正在处理…' : 'Agent 尚未返回内容。')
}

function requestId() {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `inbox-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

/**
 * A small, source-bound conversation surface for operations results. It keeps
 * the same session used to create the work item so a follow-up does not lose
 * the project/run context or create an untraceable second conversation.
 */
export function InboxConversationThread({
  conversationId,
  context,
}: InboxConversationThreadProps) {
  const conversation = useAgentConversation(conversationId)
  const send = useSendAgentConversationMessage()
  const [message, setMessage] = useState('')
  const [error, setError] = useState<string | null>(null)
  const turns = useMemo(
    () => [...(conversation.data?.turns ?? [])].sort((left, right) => left.sequence - right.sequence),
    [conversation.data?.turns],
  )
  const closed = conversation.data?.status === 'closed'

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const content = message.trim()
    if (!content || send.isPending || closed) return
    setError(null)
    try {
      await send.mutateAsync({
        conversationId,
        data: { request_id: requestId(), content, context },
      })
      setMessage('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '追问没有发送成功，请重试。')
    }
  }

  return (
    <section aria-labelledby="inbox-follow-up-heading" className="rounded-lg border bg-background" data-testid="inbox-conversation-thread">
      <div className="flex items-start gap-2 border-b bg-muted/20 px-3 py-2.5">
        <MessageCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div>
          <h3 id="inbox-follow-up-heading" className="text-xs font-medium">在当前结果上追问</h3>
          <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">回复会写入原 Agent 会话，并带回此项目、工作流和运行上下文。</p>
        </div>
      </div>

      <div className="max-h-72 space-y-3 overflow-y-auto px-3 py-3" aria-live="polite">
        {conversation.isLoading ? (
          <p className="flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-3.5 animate-spin" />正在读取原会话…</p>
        ) : conversation.isError ? (
          <p role="alert" className="text-xs text-destructive">原会话暂时无法读取。你仍可从“继续原会话”打开完整工作区。</p>
        ) : turns.length ? turns.map((turn) => (
          <article key={`${turn.sequence}-${turn.request_id}`} className="space-y-1.5 text-sm">
            <p className="rounded-md bg-muted/55 px-2.5 py-2 text-foreground">{turn.user_content}</p>
            <p className="border-l-2 border-primary/35 pl-2.5 text-sm leading-6 text-muted-foreground">{turnReply(turn)}</p>
          </article>
        )) : (
          <p className="text-xs text-muted-foreground">这条结果还没有可显示的会话记录。</p>
        )}
      </div>

      <form onSubmit={submit} className="border-t p-3">
        <label className="sr-only" htmlFor={`inbox-follow-up-${conversationId}`}>向原 Agent 追问</label>
        <Textarea
          id={`inbox-follow-up-${conversationId}`}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder={closed ? '这个会话已关闭，请在项目中创建新的工作会话。' : '例如：说明失败原因，并给出下一步可执行的修复方案'}
          disabled={send.isPending || closed}
          rows={2}
          className="resize-none text-sm"
        />
        <div className="mt-2 flex items-center justify-between gap-3">
          <span className="text-[11px] text-muted-foreground">{closed ? '会话已关闭' : '会话回复会留在这条结果里'}</span>
          <Button type="submit" size="sm" disabled={!message.trim() || send.isPending || closed}>
            {send.isPending ? <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" /> : <Send aria-hidden="true" className="size-3.5" />}
            {send.isPending ? '发送中…' : '发送追问'}
          </Button>
        </div>
        {error ? <p role="alert" className="mt-2 text-xs text-destructive">{error}</p> : null}
      </form>
    </section>
  )
}

export function InboxConversationUnavailable({ reason }: { reason?: 'missing' | 'invalid' | 'scope-mismatch' }) {
  const message = reason === 'scope-mismatch'
    ? '这个结果的会话来源不属于当前 Workspace，因此不能在这里追问。'
    : reason === 'invalid'
      ? '这个结果带有无效的会话来源，无法安全地恢复回复链。'
      : '这个结果没有可验证的原 Agent 会话，因此无法在收件箱中追问。'
  return (
    <section aria-labelledby="inbox-follow-up-unavailable-heading" className="rounded-lg border border-dashed bg-muted/10 p-3">
      <h3 id="inbox-follow-up-unavailable-heading" className="text-xs font-medium">在当前结果上追问</h3>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{message}</p>
    </section>
  )
}

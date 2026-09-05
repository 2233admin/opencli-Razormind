import { AlertTriangle, Inbox } from 'lucide-react'

import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import { loader, Matrix } from '@/components/unlumen-ui/matrix'

export function LoadingState({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-4 px-1 py-1 text-sm text-muted-foreground" role="status">
        <Matrix
          rows={7}
          cols={7}
          frames={loader}
          fps={10}
          size={5}
          gap={2}
          palette={{ on: 'var(--color-primary)', off: 'var(--color-muted-foreground)' }}
          ariaLabel="正在加载"
        />
        <span>正在读取运行状态</span>
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} aria-hidden="true" className="h-16 w-full animate-none rounded-lg" />
      ))}
    </div>
  )
}

export function ErrorState({
  message,
  hint,
  action,
}: {
  message?: string
  hint?: string
  action?: React.ReactNode
}) {
  return (
    <Empty className="border border-dashed">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <AlertTriangle className="text-destructive" />
        </EmptyMedia>
        <EmptyTitle>加载失败</EmptyTitle>
        <EmptyDescription>{message ?? '无法连接后端服务。'}</EmptyDescription>
      </EmptyHeader>
      {hint || action ? (
        <EmptyContent className="flex flex-col items-center gap-3 text-xs text-muted-foreground">
          {hint ? <span>{hint}</span> : null}
          {action}
        </EmptyContent>
      ) : null}
    </Empty>
  )
}

export function EmptyState({ title, description }: { title?: string; description?: string }) {
  return (
    <Empty className="border border-dashed">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Inbox />
        </EmptyMedia>
        <EmptyTitle>{title ?? '暂无数据'}</EmptyTitle>
        <EmptyDescription>{description ?? '当前没有可显示的内容。'}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}

/** Standard hint shown when the backend proxy isn't configured in this env. */
export const BACKEND_HINT = '未配置 BACKEND_URL 或访问令牌时，此处将为空。连接后端后即显示真实数据。'

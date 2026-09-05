'use client'

import { usePathname, useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { loader, Matrix } from '@/components/unlumen-ui/matrix'

import { useAuth } from './auth-provider'

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { status, recoveryError, recoveryMode, recoveryPending, retrySession } = useAuth()
  const pathname = usePathname()
  const router = useRouter()
  useEffect(() => {
    if (status === 'anonymous') {
      const returnTo = window.location.search ? `${pathname}${window.location.search}` : pathname
      router.replace(`/login?returnTo=${encodeURIComponent(returnTo)}`)
    }
  }, [pathname, router, status])

  if (status === 'recovering') {
    const incompatible = recoveryMode === 'incompatible'
    const recoveryCopy = incompatible
      ? {
          title: '身份校验需要处理',
          description: '登录状态已保留，但 API 拒绝了当前前端请求。系统已停止自动重试，避免重复失败。',
          errorLabel: '处理建议',
        }
      : {
          title: 'API 服务正在恢复',
          description: '登录状态已保留。健康检查和身份校验通过后，将自动回到当前页面。',
          errorLabel: '上次检查',
        }
    return (
      <div className="relative min-h-screen">
        <div inert={true}>{children}</div>
        <main className="absolute inset-0 z-[100] grid min-h-screen place-items-center bg-background/95 px-6">
          <div
            className="flex max-w-md flex-col items-center gap-4 text-center text-sm text-muted-foreground"
            role="status"
            aria-live="polite"
          >
            <Matrix
              rows={7}
              cols={7}
              frames={loader}
              fps={10}
              size={5}
              gap={2}
              palette={{ on: 'var(--color-primary)', off: 'var(--color-muted-foreground)' }}
              ariaLabel={recoveryCopy.title}
            />
            <div className="space-y-1.5">
              <p className="font-medium text-foreground">{recoveryCopy.title}</p>
              <p>{recoveryCopy.description}</p>
              {recoveryError ? (
                <p className="text-xs">{recoveryCopy.errorLabel}：{recoveryError}</p>
              ) : null}
            </div>
            <Button variant="outline" size="sm" disabled={recoveryPending} onClick={() => void retrySession()}>
              {recoveryPending ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
              {recoveryPending ? '正在检查…' : '重新检查'}
            </Button>
          </div>
        </main>
      </div>
    )
  }

  if (status !== 'authenticated') {
    return (
      <main className="grid min-h-screen place-items-center bg-background">
        <div className="flex flex-col items-center gap-4 text-sm text-muted-foreground" role="status">
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
          <span>{status === 'loading' ? '正在恢复会话…' : '正在前往登录…'}</span>
        </div>
      </main>
    )
  }

  return children
}

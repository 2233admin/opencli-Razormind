'use client'

import { KeyRound, LoaderCircle, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { useAuth } from '@/components/auth/auth-provider'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

export function LocalAdminAccess({ onAuthenticated }: { onAuthenticated: () => void }) {
  const {
    localAdminStatus,
    refreshLocalAdminStatus,
    signInWithBootstrap,
    signInWithLocal,
    setupLocalAdmin,
  } = useAuth()
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [bootstrapToken, setBootstrapToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [recoveryMode, setRecoveryMode] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (localAdminStatus === 'unconfigured' && password !== confirmation) {
      toast.error('两次输入的密码不一致')
      return
    }
    setSubmitting(true)
    try {
      if (localAdminStatus === 'configured') await signInWithLocal(password)
      else await setupLocalAdmin(bootstrapToken, password)
      toast.success(localAdminStatus === 'configured' ? '登录成功' : '本地管理员已创建')
      onAuthenticated()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '无法完成管理员验证')
      setSubmitting(false)
    }
  }

  async function recover(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      await signInWithBootstrap(bootstrapToken)
      toast.success('已通过紧急恢复验证')
      onAuthenticated()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '恢复令牌无效')
      setSubmitting(false)
    }
  }

  if (localAdminStatus === 'loading') {
    return <div className="h-24 animate-pulse rounded-lg bg-muted/50 motion-reduce:animate-none" aria-label="正在检查管理员状态" />
  }

  if (localAdminStatus === 'unavailable') {
    return (
      <div className="space-y-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4">
        <div>
          <h2 className="text-sm font-medium">无法检查管理员状态</h2>
          <p className="mt-1 text-sm text-muted-foreground">请确认后端服务已启动，然后重试。</p>
        </div>
        <Button type="button" variant="outline" className="w-full" onClick={refreshLocalAdminStatus}>
          重新检查
        </Button>
      </div>
    )
  }

  const configured = localAdminStatus === 'configured'

  return (
    <form onSubmit={recoveryMode ? recover : submit} className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">
          {recoveryMode ? '紧急恢复' : configured ? '本地管理员登录' : '创建本地管理员'}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {recoveryMode
            ? '使用部署环境中保存的 Bootstrap Admin 令牌进入控制台。'
            : configured
            ? '使用首次部署时设置的管理员密码。'
            : '首次部署只需完成一次；以后直接使用管理员密码。'}
        </p>
      </div>
      <FieldGroup>
        {!configured || recoveryMode ? (
          <Field>
            <FieldLabel htmlFor="local-bootstrap-token">
              {recoveryMode ? '紧急恢复令牌' : '首次部署令牌'}
            </FieldLabel>
            <Input
              id="local-bootstrap-token"
              type="password"
              value={bootstrapToken}
              onChange={(event) => setBootstrapToken(event.target.value)}
              autoComplete="off"
              required
            />
            <FieldDescription>
              {recoveryMode
                ? '使用部署目录 .env 中的 BOOTSTRAP_ADMIN_TOKEN。'
                : '安装程序会在完成部署后直接打印该令牌。'}
            </FieldDescription>
          </Field>
        ) : null}
        {!recoveryMode ? <Field>
          <FieldLabel htmlFor="local-admin-password">管理员密码</FieldLabel>
          <Input
            id="local-admin-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={configured ? 'current-password' : 'new-password'}
            minLength={12}
            maxLength={256}
            required
          />
          <FieldDescription>至少 12 个字符。</FieldDescription>
        </Field> : null}
        {!configured && !recoveryMode ? (
          <Field>
            <FieldLabel htmlFor="local-admin-password-confirmation">确认管理员密码</FieldLabel>
            <Input
              id="local-admin-password-confirmation"
              type="password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              required
            />
          </Field>
        ) : null}
      </FieldGroup>
      <Button type="submit" className="w-full" disabled={submitting}>
        {submitting ? <LoaderCircle className="animate-spin" /> : configured ? <KeyRound /> : <ShieldCheck />}
        {submitting ? '正在验证…' : recoveryMode ? '验证恢复令牌' : configured ? '登录控制台' : '创建管理员并进入控制台'}
      </Button>
      {configured ? (
        <Button type="button" variant="ghost" className="w-full text-muted-foreground" disabled={submitting} onClick={() => setRecoveryMode((value) => !value)}>
          {recoveryMode ? '返回密码登录' : '无法登录？使用紧急恢复令牌'}
        </Button>
      ) : null}
    </form>
  )
}

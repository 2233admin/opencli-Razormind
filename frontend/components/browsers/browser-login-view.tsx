'use client'

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Loader2, RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { type BrowserAccount, type LoginAction, confirmBrowserAccount, getBrowserLoginFrame, openBrowserLogin, renameBrowserAccount, sendBrowserLoginInput } from '@/lib/api/browser-accounts'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

const KEYS = new Set(['Enter', 'Backspace', 'Tab', 'Escape', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'])

export function BrowserLoginView({ account, onClose, onDelete }: { account: BrowserAccount; onClose: () => void; onDelete: () => void }) {
  const client = useQueryClient()
  const opened = useRef(false)
  const alive = useRef(true)
  const controller = useRef<AbortController | null>(null)
  const queue = useRef(Promise.resolve())
  const pending = useRef(0)
  const inputFailed = useRef(false)
  const inputEpoch = useRef(0)
  const fetching = useRef<Promise<unknown> | null>(null)
  const [acting, setActing] = useState(false)
  const [name, setName] = useState('')
  const [inputError, setInputError] = useState('')
  const queryKey = ['browser-login-frame', account.id]
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; controller.current?.abort() }
  }, [])
  const frame = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const request = opened.current ? getBrowserLoginFrame(account.id, signal) : openBrowserLogin(account.id, signal)
      fetching.current = request
      try { const result = await request; opened.current = true; return result }
      finally { if (fetching.current === request) fetching.current = null }
    },
    refetchInterval: acting ? false : 600,
    enabled: !acting,
    gcTime: 0,
    retry: 1,
  })
  const send = (action: LoginAction) => {
    if (!frame.data || frame.isError || inputFailed.current) return
    const epoch = inputEpoch.current
    pending.current += 1
    setActing(true)
    const canceled = fetching.current?.catch(() => {})
    queue.current = queue.current.then(async () => {
      await canceled
      try {
        if (!alive.current || epoch !== inputEpoch.current) return
        controller.current = new AbortController()
        const result = await sendBrowserLoginInput(account.id, action, controller.current.signal)
        if (alive.current) client.setQueryData(queryKey, result)
      } catch (error) {
        inputFailed.current = true
        inputEpoch.current += 1
        if (alive.current) setInputError(`${error instanceof Error ? error.message : '操作未完成'}；后续输入已暂停，请检查画面后重试并重新输入。`)
      } finally {
        pending.current -= 1
        if (alive.current && pending.current === 0) setActing(false)
      }
    })
  }
  const save = useMutation({ mutationFn: async () => {
    setActing(true)
    await fetching.current?.catch(() => {})
    const label = name.trim() || frame.data?.suggested_name?.trim()
    if (label) await renameBrowserAccount(account.id, label.slice(0, 100))
    await confirmBrowserAccount(account.id, 'confirmed')
  }, onSuccess: () => { void client.invalidateQueries({ queryKey: ['browser-accounts'] }); toast.success('已保存登录确认'); onClose() }, onError: (error: Error) => { setActing(false); toast.error(error.message) } })
  const refresh = () => {
    inputFailed.current = false
    setInputError('')
    if (frame.isError || !frame.data) {
      opened.current = false
      void frame.refetch()
    } else send({ kind: 'reload' })
  }
  return <Dialog open onOpenChange={value => { if (!value && !save.isPending) onClose() }}>
    <DialogContent className="max-h-[94dvh] overflow-y-auto sm:max-w-6xl">
      <DialogHeader><DialogTitle>登录 {account.platform}</DialogTitle><DialogDescription>直接扫描下方登录页中的二维码，或点击画面输入。登录空间已自动准备，关闭后会话仍会保留。</DialogDescription></DialogHeader>
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted px-3 py-2"><p className="min-w-0 truncate text-xs">{frame.data?.origin || account.login_url}</p><div className="flex gap-1"><Button variant="ghost" size="icon" aria-label="向上滚动登录页" disabled={!frame.data || frame.isError || acting} onClick={() => send({ kind: 'scroll', x: 200, y: 200, delta: -500 })}><ArrowUp /></Button><Button variant="ghost" size="icon" aria-label="向下滚动登录页" disabled={!frame.data || frame.isError || acting} onClick={() => send({ kind: 'scroll', x: 200, y: 200, delta: 500 })}><ArrowDown /></Button><Button variant="ghost" size="icon" aria-label="刷新登录画面" disabled={acting || frame.isPending} onClick={refresh}><RefreshCw className={acting ? 'animate-spin' : ''} /></Button></div></div>
      {acting && <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3 animate-spin" />正在操作网站并更新画面…</p>}
      {frame.isPending && <div role="status" className="flex min-h-64 items-center justify-center gap-2"><Loader2 className="animate-spin" />正在连接登录页面…</div>}
      {(frame.isError || inputError) && <p role="alert" className="text-sm text-destructive">{frame.error?.message || inputError}<Button variant="link" disabled={acting} onClick={() => { inputFailed.current = false; setInputError(''); void frame.refetch() }}>重试</Button></p>}
      {frame.data && <div role="application" aria-label="交互式登录页面" tabIndex={0} className="overflow-hidden rounded-lg border bg-white outline-none focus-visible:ring-2 focus-visible:ring-ring" onKeyDown={event => {
        if (event.ctrlKey || event.metaKey || event.nativeEvent.isComposing) return
        if (event.key.length === 1) { event.preventDefault(); send({ kind: 'text', text: event.key }) }
        else if (KEYS.has(event.key)) { event.preventDefault(); event.stopPropagation(); send({ kind: 'key', key: event.key }) }
      }} onPaste={event => { event.preventDefault(); send({ kind: 'text', text: event.clipboardData.getData('text').slice(0, 2000) }) }}>
        {/* This authenticated live frame must not go through an image cache. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img alt="实时网站登录画面，可直接扫码" src={`data:image/png;base64,${frame.data.image}`} width={frame.data.width} height={frame.data.height} draggable={false} className={`h-auto w-full ${frame.isError ? 'opacity-40' : ''}`} onClick={event => {
          const rect = event.currentTarget.getBoundingClientRect()
          event.currentTarget.parentElement?.focus()
          send({ kind: 'click', x: Math.min(frame.data!.width - 1, Math.round((event.clientX - rect.left) * frame.data!.width / rect.width)), y: Math.min(frame.data!.height - 1, Math.round((event.clientY - rect.top) * frame.data!.height / rect.height)) })
        }} />
      </div>}
      <p className="text-xs text-muted-foreground">画面会自动更新。可点击输入框后键入或粘贴；需要中文或手机输入时，也可使用下方输入工具。</p>
      <form className="flex gap-2" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const value = new FormData(form).get('login-text'); if (typeof value === 'string' && value) { send({ kind: 'text', text: value }); form.reset() } }}><Input type="password" name="login-text" aria-label="发送到登录页的文字" placeholder="先点击画面中的输入框，再在此输入或粘贴" autoComplete="off" maxLength={2000} disabled={!frame.data || frame.isError} /><Button type="submit" variant="outline" disabled={!frame.data || frame.isError || acting}>输入到页面</Button></form>
      <div className="space-y-3 border-t pt-4"><label className="block space-y-2 text-sm">备注名（可选，登录后再填）<Input value={name} onChange={event => setName(event.target.value)} placeholder={frame.data?.suggested_name || account.label} maxLength={100} /></label><div className="flex flex-wrap justify-between gap-2"><Button variant="ghost" onClick={onDelete} disabled={acting || save.isPending}><Trash2 />删除账号</Button><Button disabled={!frame.data || frame.isError || !!inputError || acting || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2 className="animate-spin" />}已完成登录，保存账号</Button></div><p className="text-xs text-muted-foreground">点击保存表示你已在网站完成登录，系统不会将画面连通误判为登录成功。</p></div>
    </DialogContent>
  </Dialog>
}

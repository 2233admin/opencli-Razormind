'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Globe, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { type BrowserAccount, createBrowserAccount, listBrowserAccounts, listLoginWebsites, removeBrowserAccount, restoreBrowserAccount } from '@/lib/api/browser-accounts'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { BrowserLoginView } from './browser-login-view'

const QUERY_KEY = ['browser-accounts']
const STATUS = { unconfirmed: '待登录确认', confirmed: '已确认登录', needs_login: '需重新登录', profile_changed: '登录环境已变更', archived: '已归档', deleting: '等待删除完成' }

function AddAccount({ onCreated }: { onCreated: (account: BrowserAccount) => void }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const client = useQueryClient()
  const websites = useQuery({ queryKey: ['login-websites'], queryFn: listLoginWebsites, enabled: open })
  const create = useMutation({ mutationFn: createBrowserAccount, onSuccess: (account) => {
    void client.invalidateQueries({ queryKey: QUERY_KEY })
    setOpen(false)
    setUrl('')
    onCreated(account)
  } })
  return <>
    <Button onClick={() => setOpen(true)}><Plus />添加账号</Button>
    <Dialog open={open} onOpenChange={value => { if (!create.isPending) setOpen(value) }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader><DialogTitle>登录哪个网站？</DialogTitle><DialogDescription>选择常用网站或输入网址。系统会自动准备独立登录空间，你只需完成登录。</DialogDescription></DialogHeader>
        <form className="space-y-4" onSubmit={event => { event.preventDefault(); if (url.trim() && !create.isPending) create.mutate({ site_url: url.trim() }) }}>
          <label className="block space-y-2 text-sm">网站地址<Input aria-label="网站地址" placeholder="例如 github.com/login，也可以粘贴登录链接" value={url} onChange={event => setUrl(event.target.value)} maxLength={2048} disabled={create.isPending} /></label>
          <Button type="submit" disabled={!url.trim() || create.isPending}>继续登录</Button>
        </form>
        <div className="border-t pt-4"><p className="mb-3 text-sm text-muted-foreground">常用网站 · 不限于以下网站</p><div className="flex flex-wrap gap-2">{websites.data?.map(site => <Button key={site.url} variant="outline" disabled={create.isPending} onClick={() => create.mutate({ site_url: site.url })}>{site.label}</Button>)}</div></div>
        {create.isPending && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin" />正在自动准备登录空间…</p>}
        {create.isError && <p role="alert" className="text-sm text-destructive">{create.error.message}</p>}
      </DialogContent>
    </Dialog>
  </>
}

function DeleteAccount({ account, onClose }: { account: BrowserAccount; onClose: () => void }) {
  const [clear, setClear] = useState(true)
  const client = useQueryClient()
  const remove = useMutation({ mutationFn: () => removeBrowserAccount(account.id, clear), onSuccess: () => {
    void client.invalidateQueries({ queryKey: QUERY_KEY })
    toast.success(clear ? '账号和登录数据已删除' : '账号已删除，登录数据独立保留')
    onClose()
  } })
  return <Dialog open onOpenChange={value => { if (!value && !remove.isPending) onClose() }}>
    <DialogContent><DialogHeader><DialogTitle>删除账号</DialogTitle><DialogDescription>确定删除「{account.label}」？账号将从列表移除。</DialogDescription></DialogHeader>
      <fieldset className="space-y-3" disabled={remove.isPending}><legend className="mb-2 text-sm font-medium">如何处理登录数据</legend>
        <label className="flex items-start gap-3 rounded-lg border p-3 text-sm"><input type="radio" name="login-data" checked={clear} onChange={() => setClear(true)} className="mt-1" /><span>同时清除登录数据<span className="mt-1 block text-muted-foreground">删除保存的会话与专属登录空间，无法撤销。</span></span></label>
        <label className="flex items-start gap-3 rounded-lg border p-3 text-sm"><input type="radio" name="login-data" checked={!clear} onChange={() => setClear(false)} className="mt-1" /><span>保留登录数据<span className="mt-1 block text-muted-foreground">仅删除账号记录。原登录空间仍独立保留，不会分配给其他任务。</span></span></label>
      </fieldset>
      {remove.isError && <p role="alert" className="text-sm text-destructive">{remove.error.message}</p>}
      <div className="flex justify-end gap-2"><Button variant="outline" onClick={onClose} disabled={remove.isPending}>取消</Button><Button variant="destructive" onClick={() => remove.mutate()} disabled={remove.isPending}>{remove.isPending && <Loader2 className="animate-spin" />}确认删除</Button></div>
    </DialogContent>
  </Dialog>
}

export function BrowserAccountsPanel() {
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<BrowserAccount | null>(null)
  const [deleting, setDeleting] = useState<BrowserAccount | null>(null)
  const query = useQuery({ queryKey: QUERY_KEY, queryFn: listBrowserAccounts, refetchInterval: 30_000 })
  const restore = useMutation({ mutationFn: restoreBrowserAccount, onSuccess: () => { void query.refetch() }, onError: (error: Error) => toast.error(error.message) })
  const accounts = [...(query.data?.accounts ?? []), ...(query.data?.archived_accounts ?? [])]
  const filtered = accounts.filter(account => `${account.label} ${account.platform}`.toLowerCase().includes(search.toLowerCase()))
  return <div className="space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><Input className="max-w-sm" aria-label="搜索账号" placeholder="搜索账号或网站" value={search} onChange={event => setSearch(event.target.value)} /><div className="flex gap-2"><Button variant="outline" size="icon" aria-label="刷新账号" onClick={() => void query.refetch()}><RefreshCw className={query.isFetching ? 'animate-spin' : ''} /></Button><AddAccount onCreated={setSelected} /></div></div>
    {query.isPending ? <p role="status" className="py-12 text-center text-muted-foreground">正在加载账号…</p> : query.isError ? <div role="alert" className="rounded-xl border p-6"><p>加载失败：{query.error.message}</p><Button className="mt-3" variant="outline" onClick={() => void query.refetch()}>重试</Button></div> : filtered.length === 0 ? <div className="rounded-xl border border-dashed px-6 py-16 text-center"><Globe className="mx-auto mb-4 size-8 text-muted-foreground" /><p className="font-medium">{accounts.length ? '没有找到匹配的账号' : '登录你的第一个账号'}</p><p className="mt-2 text-sm text-muted-foreground">{accounts.length ? '试试其他账号名称或网站地址。' : '选择网站，在这里扫码或输入登录。同一网站可以添加多个账号。'}</p></div> : <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{filtered.map(account => <Card key={account.id}><CardContent className="space-y-4 pt-5">
      <div className="flex items-start justify-between gap-3"><div className="min-w-0"><h2 className="truncate font-medium">{account.label}</h2><p className="mt-1 truncate text-sm text-muted-foreground">{account.platform}</p></div><Button variant="ghost" size="icon" aria-label={`删除 ${account.label}`} onClick={() => setDeleting(account)}><Trash2 className="size-4" /></Button></div>
      <Badge variant="secondary">{STATUS[account.status]}</Badge>
      {account.status === 'archived' ? <Button variant="outline" className="w-full" disabled={restore.isPending} onClick={() => restore.mutate(account.id)}>恢复账号</Button> : <Button variant="outline" className="w-full" disabled={account.status === 'deleting'} onClick={() => setSelected(account)}>{account.status === 'confirmed' ? '查看登录页面' : '继续登录'}</Button>}
    </CardContent></Card>)}</div>}
    <p className="text-xs text-muted-foreground">登录会话由系统独立保存；网站可能要求重新验证。完成登录后可自选备注名。</p>
    {selected && <BrowserLoginView key={selected.id} account={selected} onClose={() => setSelected(null)} onDelete={() => { setDeleting(selected); setSelected(null) }} />}
    {deleting && <DeleteAccount key={deleting.id} account={deleting} onClose={() => setDeleting(null)} />}
  </div>
}

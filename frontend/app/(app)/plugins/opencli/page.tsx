'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft,
  Check,
  ChevronRight,
  CircleAlert,
  Globe2,
  KeyRound,
  Loader2,
  MonitorUp,
  RefreshCw,
  Search,
} from 'lucide-react'

import { EmptyState } from '@/components/shell/data-states'
import { PageContainer } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  OPENCLI_SITE_CATEGORIES,
  type OpenCLIAdapterPlugin,
  type OpenCLISiteCategory,
} from '@/lib/plugins/opencli-adapter-catalog'
import { useOpenCLIAdapterRegistry } from '@/lib/plugins/use-opencli-adapter-registry'
import { cn } from '@/lib/utils'

type SiteCategoryFilter = 'all' | OpenCLISiteCategory

function metricLabel(value: number, label: string) {
  return (
    <div className="rounded-lg border bg-muted/15 px-3 py-2.5">
      <div className="font-mono text-lg font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-3xs text-muted-foreground">{label}</div>
    </div>
  )
}

function AdapterDetails({ plugin }: { plugin: OpenCLIAdapterPlugin }) {
  return (
    <SheetContent className="w-[95vw] sm:max-w-xl">
      <SheetHeader className="border-b pr-12">
        <div className="flex items-start gap-3">
          <div className="grid size-11 shrink-0 place-items-center rounded-xl border bg-muted/35">
            <Globe2 aria-hidden="true" className="size-5" />
          </div>
          <div className="min-w-0">
            <SheetTitle>{plugin.label}</SheetTitle>
            <SheetDescription className="mt-1 font-mono">{plugin.domains[0] ?? plugin.site}</SheetDescription>
          </div>
        </div>
      </SheetHeader>

      <div className="space-y-5 overflow-y-auto px-4 pb-6">
        <section>
          <h3 className="text-xs font-semibold">网站适配说明</h3>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {plugin.introduction ?? plugin.description}
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {plugin.browserRequired ? <Badge variant="outline"><MonitorUp className="mr-1 size-3" />需要浏览器</Badge> : null}
            {plugin.loginRequired ? <Badge variant="outline"><KeyRound className="mr-1 size-3" />需要登录</Badge> : null}
            <Badge variant="outline">{plugin.readCount} 读取</Badge>
            <Badge variant="outline">{plugin.writeCount} 操作</Badge>
          </div>
        </section>

        <section>
          <div className="mb-2 flex items-center justify-between gap-3">
            <h3 className="text-xs font-semibold">提供的命令</h3>
            <span className="font-mono text-3xs text-muted-foreground">{plugin.commands.length}</span>
          </div>
          <div className="divide-y rounded-lg border">
            {plugin.commands.map((command) => (
              <div key={command.id} className="flex items-start gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-medium">{command.label}</div>
                  <div className="mt-1 text-3xs leading-4 text-muted-foreground">
                    {command.description || `${command.site} ${command.command}`}
                  </div>
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    'h-5 shrink-0 px-1.5 text-3xs',
                    command.access === 'read'
                      ? 'border-success/30 bg-success/5 text-success'
                      : 'border-warning/30 bg-warning/5 text-warning',
                  )}
                >
                  {command.access === 'read' ? '读取' : '操作'}
                </Badge>
              </div>
            ))}
          </div>
        </section>

        <Button className="w-full" nativeButton={false} render={<Link href={`/studio?${new URLSearchParams({ provider: 'opencli', adapter: plugin.id }).toString()}`} />}>
          带着能力进入 Studio
        </Button>
      </div>
    </SheetContent>
  )
}

export default function OpenCLIProviderPage() {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<SiteCategoryFilter>('all')
  const [selectedPlugin, setSelectedPlugin] = useState<OpenCLIAdapterPlugin | null>(null)
  const { plugins, summary, error, loading, refresh } = useOpenCLIAdapterRegistry(true)

  const filteredPlugins = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return plugins.filter((plugin) => {
      if (category !== 'all' && plugin.siteCategory !== category) return false
      if (!needle) return true
      return [
        plugin.label,
        plugin.site,
        ...plugin.domains,
        ...plugin.features,
        ...plugin.commands.map((command) => `${command.label} ${command.command} ${command.description}`),
      ].join(' ').toLowerCase().includes(needle)
    })
  }, [category, plugins, query])

  const categoryCounts = useMemo(() => {
    const counts = new Map<OpenCLISiteCategory, number>()
    for (const plugin of plugins) counts.set(plugin.siteCategory, (counts.get(plugin.siteCategory) ?? 0) + 1)
    return counts
  }, [plugins])

  const actions = (
    <div className="flex items-center gap-2">
      <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/plugins" />}>
        <ArrowLeft aria-hidden="true" className="size-4" />
        返回插件
      </Button>
      <Button variant="outline" size="sm" onClick={() => refresh()} disabled={loading}>
        {loading ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <RefreshCw aria-hidden="true" className="size-4" />}
        刷新目录
      </Button>
    </div>
  )

  return (
    <PageContainer
      eyebrow="OpenCLI Provider"
      title="网站适配"
      description="查看 OpenCLI 已注册的网站、读取能力、操作命令和运行要求；节点仍在 Studio 中按需使用。"
      actions={actions}
    >
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {metricLabel(summary.adapterCount, '网站适配')}
        {metricLabel(summary.commandCount, '命令')}
        {metricLabel(summary.parameterReadyCount, '可直接使用')}
        {metricLabel(summary.configurationRequiredCount, '需要参数或配置')}
      </div>

      {error ? (
        <div className="mt-4 flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning" />
          <div>
            <div className="font-medium">网站适配目录暂时不可用</div>
            <p className="mt-1 text-muted-foreground">{error}</p>
          </div>
        </div>
      ) : null}

      <section className="mt-5 overflow-hidden rounded-xl border bg-background">
        <div className="border-b p-3">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div className="relative min-w-0 flex-1">
              <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="min-h-10 pl-9"
                placeholder="搜索网站、域名或能力"
                aria-label="搜索网站、域名或能力"
              />
            </div>
            <div className="font-mono text-3xs text-muted-foreground">
              {filteredPlugins.length} / {plugins.length} 个网站
            </div>
          </div>
          <nav aria-label="网站分类" className="no-scrollbar mt-3 flex gap-1 overflow-x-auto">
            <button
              type="button"
              onClick={() => setCategory('all')}
              className={cn(
                'min-h-8 shrink-0 rounded-md px-3 text-xs transition-colors',
                category === 'all' ? 'bg-foreground text-background' : 'bg-muted/45 text-muted-foreground hover:text-foreground',
              )}
            >
              全部 {plugins.length}
            </button>
            {OPENCLI_SITE_CATEGORIES.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setCategory(item.key)}
                className={cn(
                  'min-h-8 shrink-0 rounded-md px-3 text-xs transition-colors',
                  category === item.key ? 'bg-foreground text-background' : 'bg-muted/45 text-muted-foreground hover:text-foreground',
                )}
              >
                {item.label} {categoryCounts.get(item.key) ?? 0}
              </button>
            ))}
          </nav>
        </div>

        {loading && plugins.length === 0 ? (
          <div className="grid min-h-64 place-items-center">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              正在读取 OpenCLI 网站目录
            </div>
          </div>
        ) : filteredPlugins.length ? (
          <div className="grid gap-px bg-border sm:grid-cols-2 xl:grid-cols-3">
            {filteredPlugins.map((plugin) => (
              <button
                key={plugin.id}
                type="button"
                onClick={() => setSelectedPlugin(plugin)}
                className="group flex min-h-32 flex-col bg-background p-4 text-left transition-colors hover:bg-muted/25"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="grid size-9 place-items-center rounded-lg border bg-muted/35">
                    <Globe2 aria-hidden="true" className="size-4" />
                  </div>
                  <ChevronRight aria-hidden="true" className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                </div>
                <div className="mt-3 min-w-0">
                  <div className="truncate text-sm font-semibold">{plugin.label}</div>
                  <div className="mt-1 truncate font-mono text-3xs text-muted-foreground">
                    {plugin.domains[0] ?? plugin.site}
                  </div>
                </div>
                <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 pt-3 text-3xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1"><Check className="size-3 text-success" />{plugin.commandCount} 命令</span>
                  <span>{plugin.readCount} 读取</span>
                  {plugin.writeCount ? <span>{plugin.writeCount} 操作</span> : null}
                </div>
              </button>
            ))}
          </div>
        ) : (
          <EmptyState title="没有匹配的网站适配" description="切换分类或调整搜索关键词后再试。" />
        )}
      </section>

      <Sheet open={selectedPlugin !== null} onOpenChange={(open) => { if (!open) setSelectedPlugin(null) }}>
        {selectedPlugin ? <AdapterDetails plugin={selectedPlugin} /> : null}
      </Sheet>
    </PageContainer>
  )
}

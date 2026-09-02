'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleAlert,
  Clock3,
  Database,
  Download,
  Globe2,
  Loader2,
  Package,
  Puzzle,
  Rss,
  Search,
  Webhook,
  Wrench,
} from 'lucide-react'

import { toast } from 'sonner'

import { DifyPackageImportDialog } from '@/components/plugins/dify-package-import-dialog'
import { RssCatalogImportDialog } from '@/components/plugins/rss-catalog-import-dialog'
import { TemplateCatalog } from '@/components/plugins/template-catalog'
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
  PLUGIN_PROVIDER_CATEGORIES,
  PLUGIN_PROVIDERS,
  pluginProviderCategoryLabel,
  type PluginProvider,
  type PluginProviderCategory,
  type PluginProviderIcon,
} from '@/lib/plugins/provider-catalog'
import {
  updatePluginInstallation,
  useBackendPluginCatalog,
  type BackendPluginInstallation,
} from '@/lib/plugins/backend-plugin-catalog'
import { useGovernedWorkspaces, useMyWorkspaces } from '@/lib/api/hooks'
import {
  NODE_CAPABILITY_CATALOG_QUERY_KEY,
  nodeCapabilityReadinessLabel,
  nodeCapabilityReadinessTone,
  type BackendNodeCapabilityCatalog,
  type BackendNodeCapabilityDefinition,
  type BackendNodeCapabilityReadiness,
} from '@/lib/plugins/backend-node-capabilities'
import { useOpenCLIAdapterRegistry } from '@/lib/plugins/use-opencli-adapter-registry'
import { cn } from '@/lib/utils'
import { backendNodeCapabilityIsRunnable } from '@/lib/workflow/backend-node-capability-adapter'
import { getWorkflowNodeCatalog, type WorkflowNodeCatalogItem } from '@/lib/workflow/node-catalog'
import { localizeNodeText } from '@/lib/workflow/node-i18n'
import { useWorkflowCapabilities } from '@/lib/workflow/use-workflow-capabilities'
type PluginPageTab = 'installed' | 'capabilities' | 'marketplace'
type PluginSubtype = 'source' | 'template' | 'tool' | 'agent' | 'trigger' | 'extension'
type PluginCategoryFilter = 'all' | PluginProviderCategory

type ProviderState = 'ready' | 'partial' | 'configuration' | 'unavailable' | 'marketplace'
type RegistryPluginProvider = PluginProvider & {
  installation?: BackendPluginInstallation
  backendUnavailable?: boolean
  nodeCatalog?: boolean
  catalogCategories?: PluginCategoryFilter[]
}

type ProviderNodeView = {
  id: string
  label: string
  description: string
  category: string
  readiness: BackendNodeCapabilityReadiness
  runtimeReady: boolean
  missing: string[]
}

const PROVIDER_ICONS: Record<PluginProviderIcon, typeof Wrench> = {
  brain: BrainCircuit,
  wrench: Wrench,
  database: Database,
  bot: Bot,
  clock: Clock3,
  puzzle: Puzzle,
  package: Package,
  globe: Globe2,
  rss: Rss,
  webhook: Webhook,
}

const BUNDLED_PROVIDER_ID_BY_KEY: Record<string, string> = {
  'opencli-admin/opencli-adapters': 'opencli',
  'opencli-admin/native-data-sources': 'rss-reader',
  'opencli-admin/http-api': 'http-api',
  'opencli-admin/model-runtime': 'model-runtime',
  'opencli-admin/agent-runtime': 'agent-runtime',
  'opencli-admin/schedule-trigger': 'schedule-trigger',
  'opencli-admin/delivery': 'delivery',
  'opencli-admin/dify-graphon-runtime': 'workflow-core',
  'opencli-admin/workflow-bundles': 'workflow-bundles',
}

function isPluginPageTab(value: string | null): value is PluginPageTab {
  return value === 'installed' || value === 'capabilities' || value === 'marketplace'
}
function isPluginSubtype(value: string | null): value is PluginSubtype {
  return value === 'source'
    || value === 'template'
    || value === 'tool'
    || value === 'agent'
    || value === 'trigger'
    || value === 'extension'
}

function isPluginCategory(value: string | null): value is PluginCategoryFilter {
  return PLUGIN_PROVIDER_CATEGORIES.some((item) => item.key === value)
}
function providerMatchesSubtype(
  provider: RegistryPluginProvider,
  subtype: PluginSubtype,
): boolean {
  if (provider.nodeCatalog) {
    const category = subtype === 'source'
      ? 'datasource'
      : subtype === 'tool'
        ? 'tool'
        : subtype === 'agent'
          ? 'agent'
          : subtype === 'trigger'
            ? 'trigger'
            : subtype === 'extension'
              ? 'extension'
              : null
    return category !== null && provider.catalogCategories?.includes(category) === true
  }
  if (subtype === 'source') return provider.category === 'datasource'
  if (subtype === 'tool') return provider.category === 'tool'
  if (subtype === 'agent') return provider.category === 'agent'
  if (subtype === 'trigger') return provider.category === 'trigger'
  if (subtype === 'extension') {
    return provider.category === 'extension' || provider.category === 'bundle'
  }
  return false
}

function providerState(
  provider: RegistryPluginProvider,
  nodes: ProviderNodeView[],
  opencliAdapterCount: number,
): ProviderState {
  if (provider.marketplace) return 'marketplace'
  if (provider.backendUnavailable) return 'unavailable'
  if (provider.installation) {
    if (provider.installation.runtimeStatus !== 'READY') return 'configuration'
    if (nodes.length > 0 && nodes.every((node) => !nodeCapabilityIsUsable(node))) {
      return 'configuration'
    }
    if (nodes.some((node) => !nodeCapabilityIsUsable(node))) return 'partial'
    return 'ready'
  }
  if (provider.id === 'opencli' && opencliAdapterCount > 0) return 'ready'

  if (nodes.length > 0 && nodes.every(nodeCapabilityIsUsable)) return 'ready'
  if (nodes.some(nodeCapabilityIsUsable)) return 'partial'
  if (nodes.length > 0) return 'configuration'
  return 'unavailable'
}

function providerStateLabel(state: ProviderState): string {
  if (state === 'ready') return '可用'
  if (state === 'partial') return '部分可用'
  if (state === 'configuration') return '需要配置或适配'
  if (state === 'marketplace') return '可安装'
  return '尚未就绪'
}

function providerStateTone(state: ProviderState): string {
  if (state === 'ready') return 'border-success/35 bg-success/10 text-success'
  if (state === 'partial') return 'border-warning/35 bg-warning/10 text-warning'
  if (state === 'configuration') return 'border-warning/35 bg-warning/10 text-warning'
  if (state === 'marketplace') return 'border-foreground/15 bg-muted/45 text-foreground'
  return 'border-border bg-muted/30 text-muted-foreground'
}

function PluginSubtypeTabs({
  active,
  onSelect,
}: {
  active: PluginSubtype
  onSelect: (tab: PluginSubtype) => void
}) {
  const tabs: Array<[PluginSubtype, string]> = [
    ['source', '源库'],
    ['template', '模板'],
    ['tool', '工具'],
    ['agent', 'Agent'],
    ['trigger', '触发器'],
    ['extension', '扩展'],
  ]
  return (
    <nav aria-label="插件类型" className="no-scrollbar overflow-x-auto">
      <div className="inline-flex min-w-max items-center gap-1 rounded-lg bg-muted p-1">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-current={active === key ? 'page' : undefined}
            onClick={() => onSelect(key)}
            className={cn(
              'relative min-h-10 rounded-md px-4 text-sm font-medium transition-colors',
              active === key
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
          </button>
        ))}
      </div>
    </nav>
  )
}
function PluginPageTabs({
  active,
  onSelect,
}: {
  active: PluginPageTab
  onSelect: (tab: PluginPageTab) => void
}) {
  const tabs: Array<[PluginPageTab, string]> = [
    ['installed', '已安装'],
    ['capabilities', '能力目录'],
    ['marketplace', '探索市场'],
  ]
  return (
    <nav aria-label="插件中心视图" className="no-scrollbar overflow-x-auto">
      <div className="inline-flex min-w-max items-center gap-1 rounded-lg bg-muted p-1">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-current={active === key ? 'page' : undefined}
            onClick={() => onSelect(key)}
            className={cn(
              'relative min-h-10 rounded-md px-4 text-sm font-medium transition-colors',
              active === key
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
          </button>
        ))}
      </div>
    </nav>
  )
}


function ProviderCard({
  provider,
  state,
  metric,
  onOpen,
}: {
  provider: RegistryPluginProvider
  state: ProviderState
  metric: string
  onOpen: () => void
}) {
  const Icon = PROVIDER_ICONS[provider.icon]
  return (
    <article className="group rounded-md border bg-background transition-[border-color,background-color,transform] hover:-translate-y-0.5 hover:border-foreground/20 hover:bg-muted/15">
      <button
        type="button"
        onClick={onOpen}
        className="flex min-h-36 w-full flex-col p-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        aria-label={`查看 ${provider.name} 插件`}
      >
        <div className="flex w-full items-start justify-between gap-3">
          <div className="grid size-11 place-items-center rounded-md border bg-muted/35">
            <Icon aria-hidden="true" className="size-5 text-foreground" />
          </div>
          <Badge variant="outline" className={cn('h-5 px-1.5 text-3xs', providerStateTone(state))}>
            {state === 'ready' ? <Check aria-hidden="true" className="mr-1 size-3" /> : null}
            {providerStateLabel(state)}
          </Badge>
        </div>
        <div className="mt-4 min-w-0">
          <h2 className="truncate text-sm font-semibold">{provider.name}</h2>
          <p className="mt-1 text-3xs text-muted-foreground">{provider.author}</p>
          <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">{provider.description}</p>
        </div>
        <div className="mt-auto flex w-full items-center justify-between pt-4 text-3xs text-muted-foreground">
          <span>{pluginProviderCategoryLabel(provider.category)}</span>
          <span className="flex items-center gap-1">
            {metric}
            <ChevronRight aria-hidden="true" className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </span>
        </div>
      </button>
    </article>
  )
}

function CapabilityMetric({
  label,
  value,
  detail,
}: {
  label: string
  value: number
  detail: string
}) {
  return (
    <div className="rounded-md border bg-muted/15 px-3 py-2.5">
      <div className="text-3xs text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-end justify-between gap-3">
        <span className="font-mono text-lg font-semibold tabular-nums">{value}</span>
        <span className="pb-0.5 text-3xs text-muted-foreground">{detail}</span>
      </div>
    </div>
  )
}

function ProviderDetails({
  provider,
  nodes,
  state,
  opencliAdapterCount,
  workspaceId,
  onImportRss,
  onToggleInstallation,
}: {
  provider: RegistryPluginProvider
  nodes: ProviderNodeView[]
  state: ProviderState
  opencliAdapterCount: number
  workspaceId: string | null
  onImportRss: () => void
  onToggleInstallation: (installation: BackendPluginInstallation) => void
}) {
  const Icon = PROVIDER_ICONS[provider.icon]
  const installation = provider.installation
  const hasRunnableNode = nodes.some(nodeCapabilityIsUsable)
  const hasComposedNode = nodes.some((node) => node.readiness === 'composed')

  return (
    <SheetContent className="w-[94vw] sm:max-w-lg">
      <SheetHeader className="border-b pr-12">
        <div className="flex items-start gap-3">
          <div className="grid size-11 shrink-0 place-items-center rounded-md border bg-muted/35">
            <Icon aria-hidden="true" className="size-5" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <SheetTitle>{provider.name}</SheetTitle>
              <Badge variant="outline" className={cn('h-5 px-1.5 text-3xs', providerStateTone(state))}>
                {providerStateLabel(state)}
              </Badge>
            </div>
            <SheetDescription className="mt-1">
              {provider.author} · {pluginProviderCategoryLabel(provider.category)}
            </SheetDescription>
          </div>
        </div>
      </SheetHeader>

      <div className="space-y-6 overflow-y-auto px-4 pb-6">
        <section>
          <h3 className="text-xs font-semibold">插件说明</h3>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{provider.description}</p>
        </section>

        {installation ? (
          <section className="grid grid-cols-2 gap-2 rounded-lg border bg-muted/20 p-3 text-xs">
            <div>
              <div className="text-muted-foreground">版本</div>
              <div className="mt-1 font-mono">{installation.version}</div>
            </div>
            <div>
              <div className="text-muted-foreground">包来源</div>
              <div className="mt-1 font-mono">{installation.sourceKind}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Manifest 规范</div>
              <div className="mt-1 font-mono">{installation.manifestSpecVersion}</div>
            </div>
            <div>
              <div className="text-muted-foreground">签名状态</div>
              <div className="mt-1 font-mono">
                {installation.signatureState === 'present_unverified'
                  ? '存在，未验证'
                  : installation.signatureState === 'unsigned'
                    ? '未签名'
                    : '系统内置'}
              </div>
            </div>
            {!installation.bundled ? (
              <Button
                variant="outline"
                className="col-span-2 min-h-9"
                onClick={() => onToggleInstallation(installation)}
              >
                {installation.enabled ? '停用此工作区插件' : '启用此工作区插件'}
              </Button>
            ) : null}
          </section>
        ) : null}

        {installation && installation.capabilities.length > 0 ? (
          <section>
            <div className="mb-2 flex items-center justify-between gap-3">
              <h3 className="text-xs font-semibold">声明的能力</h3>
              <span className="font-mono text-3xs text-muted-foreground">
                {installation.capabilities.length}
              </span>
            </div>
            <div className="divide-y rounded-lg border">
              {installation.capabilities.map((capability) => (
                <div key={capability.id} className="flex items-start gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium">{capability.label}</div>
                    <div className="mt-0.5 truncate font-mono text-3xs text-muted-foreground">
                      {capability.family} · {capability.key}
                    </div>
                    {capability.blockers[0]?.message ? (
                      <p className="mt-1 text-3xs leading-4 text-muted-foreground">
                        {capability.blockers[0].message}
                      </p>
                    ) : null}
                  </div>
                  <Badge
                    variant="outline"
                    className={cn(
                      'h-5 shrink-0 px-1.5 text-3xs',
                      capability.status === 'READY'
                        ? 'border-success/35 bg-success/10 text-success'
                        : 'border-warning/35 bg-warning/10 text-warning',
                    )}
                  >
                    {capability.status}
                  </Badge>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {installation && Object.keys(installation.permissions).length > 0 ? (
          <section>
            <h3 className="text-xs font-semibold">权限与凭证声明</h3>
            <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap break-all rounded-lg border bg-muted/20 p-3 text-3xs leading-5 text-muted-foreground">
              {JSON.stringify(installation.permissions, null, 2)}
            </pre>
          </section>
        ) : null}

        {installation && installation.blockers.length > 0 ? (
          <section className="rounded-lg border border-warning/30 bg-warning/5 p-3">
            <h3 className="text-xs font-semibold text-warning">运行前置条件</h3>
            <ul className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">
              {installation.blockers.map((blocker) => (
                <li key={blocker.code}>· {blocker.message}</li>
              ))}
            </ul>
          </section>
        ) : null}

        {provider.id === 'opencli' ? (
          <section className="space-y-3 rounded-lg border bg-muted/20 p-3">
            <div>
              <div className="text-3xs text-muted-foreground">已注册网站</div>
              <div className="mt-1 font-mono text-xl font-semibold tabular-nums">{opencliAdapterCount}</div>
            </div>
            <Button variant="outline" className="w-full" nativeButton={false} render={<Link href="/plugins/opencli" />}>
              浏览网站适配与命令
            </Button>
          </section>
        ) : null}

        {provider.id === 'rss-reader' ? (
          <section className="rounded-lg border bg-muted/20 p-3">
            <h3 className="text-xs font-semibold">批量接入订阅</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              RSS 节点可直接填写地址；已有 OPML 清单时，也可以一次导入多个订阅。
            </p>
            <Button variant="outline" className="mt-3 w-full" onClick={onImportRss}>
              <Download aria-hidden="true" className="size-4" />
              导入 OPML 订阅清单
            </Button>
          </section>
        ) : null}

        {provider.marketplace ? (
          <section className="rounded-lg border border-dashed p-4">
            <h3 className="text-sm font-medium">插件运行时尚未接入</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              这里展示市场入口；接入真实安装服务后才能下载、安装并注册该 Provider。
            </p>
          </section>
        ) : (
          <section>
            <div className="mb-2 flex items-center justify-between gap-3">
              <h3 className="text-xs font-semibold">提供的工作流能力</h3>
              <span className="font-mono text-3xs text-muted-foreground">{nodes.length}</span>
            </div>
            <div className="divide-y rounded-lg border">
              {nodes.map((node) => {
                const text = localizeNodeText(node.id, { label: node.label, description: node.description }, 'zh-CN')
                return (
                  <div key={node.id} className="flex items-center gap-3 px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium">{text.label}</div>
                      <div className="mt-0.5 truncate font-mono text-3xs text-muted-foreground">
                        {node.category} · {node.id}
                      </div>
                      {node.missing[0] ? (
                        <p className="mt-1 text-3xs leading-4 text-muted-foreground">
                          缺少：{node.missing.join('、')}
                        </p>
                      ) : null}
                    </div>
                    <Badge
                      variant="outline"
                      className={cn('h-5 shrink-0 px-1.5 text-3xs', nodeCapabilityReadinessTone(node.readiness))}
                    >
                      {nodeCapabilityReadinessLabel(node.readiness)}
                    </Badge>
                  </div>
                )
              })}
              {nodes.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">安装后由 Provider 注册工具。</div>
              ) : null}
            </div>
          </section>
        )}

        {!provider.marketplace && installation?.runtimeStatus !== 'BLOCKED' && hasRunnableNode ? (
          <Button
            className="w-full"
            nativeButton={false}
            render={<Link href={workspaceId ? `/studio?workspace=${encodeURIComponent(workspaceId)}` : '/studio'} />}
          >
            在工作流中使用
          </Button>
        ) : installation?.runtimeStatus === 'BLOCKED' ? (
          <Button className="w-full" disabled title="需要兼容的 OpenCLI 运行适配器">
            能力已登记，等待运行适配器
          </Button>
        ) : hasComposedNode ? (
          <Button className="w-full" disabled title="组合依赖与运行绑定尚未全部验证">
            组合方案可预览，等待依赖就绪
          </Button>
        ) : (
          <Button className="w-full" disabled title="插件安装运行时尚未接入">
            安装运行时待接入
          </Button>
        )}
      </div>
    </SheetContent>
  )
}

export default function PluginHubPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const workspaces = useGovernedWorkspaces()
  const legacyWorkspaces = useMyWorkspaces()
  const rawTab = searchParams.get('tab')
  const rawCategory = searchParams.get('category')
  const rawSubtype = searchParams.get('type')
  const activeTab: PluginPageTab = isPluginPageTab(rawTab) ? rawTab : 'installed'
  const activeSubtype: PluginSubtype = isPluginSubtype(rawSubtype)
    ? rawSubtype
    : rawTab === 'capabilities'
      ? 'tool'
      : 'source'
  const activeCategory: PluginCategoryFilter = isPluginCategory(rawCategory) ? rawCategory : 'all'
  const workspaceParam = searchParams.get('workspace')
  const legacyWorkspace = legacyWorkspaces.data?.find((workspace) => workspace.id === workspaceParam)
  const workspaceId = workspaces.data?.find((workspace) =>
    workspace.id === workspaceParam
      || (legacyWorkspace !== undefined && (
        workspace.slug === legacyWorkspace.slug
        || `governed-${workspace.slug}` === legacyWorkspace.slug
      )),
  )?.id ?? workspaces.data?.[0]?.id ?? null
  const workspaceError = workspaces.error instanceof Error
    ? workspaces.error.message
    : workspaces.error
      ? '工作区读取失败'
      : null
  const [query, setQuery] = useState('')
  const [selectedProvider, setSelectedProvider] = useState<RegistryPluginProvider | null>(null)
  const [rssImportOpen, setRssImportOpen] = useState(false)
  const [difyImportOpen, setDifyImportOpen] = useState(false)
  const {
    installations,
    error: pluginError,
    loading: pluginLoading,
  } = useBackendPluginCatalog(workspaceId)
  const {
    capabilities,
    nodeCatalog,
    error: capabilityError,
    catalogError,
    loading: capabilityLoading,
  } = useWorkflowCapabilities(true, workspaceId)
  const {
    summary,
    error: opencliError,
    loading: opencliLoading,
  } = useOpenCLIAdapterRegistry(true)
  const nodes = useMemo(() => getWorkflowNodeCatalog('intelligence', capabilities), [capabilities])
  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])
  const nodeCatalogCounts = useMemo(() => {
    const catalogNodes = nodeCatalog?.nodes ?? []
    const runnable = catalogNodes.filter(backendNodeCapabilityIsRunnable).length
    const composed = catalogNodes.filter((node) => node.readiness === 'composed').length
    return {
      runnable,
      composed,
      pending: Math.max(0, catalogNodes.length - runnable - composed),
    }
  }, [nodeCatalog])

  function updateRoute(next: { tab?: PluginPageTab; category?: PluginCategoryFilter }) {
    const params = new URLSearchParams(searchParams.toString())
    const tab = next.tab ?? activeTab
    const category = next.category ?? activeCategory
    if (tab === 'installed') params.delete('tab')
    else params.set('tab', tab)
    if (category === 'all') params.delete('category')
    else params.set('category', category)
    const queryString = params.toString()
    router.push(queryString ? `/plugins?${queryString}` : '/plugins', { scroll: false })
  }

  function updateSubtype(type: PluginSubtype) {
    const params = new URLSearchParams(searchParams.toString())
    params.delete('tab')
    params.set('type', type)
    router.push(`/plugins?${params.toString()}`, { scroll: false })
  }

  const availableProviders = (() => {
    const source: RegistryPluginProvider[] = activeTab === 'marketplace'
      ? []
      : activeTab === 'capabilities'
        ? nodeCatalog?.nodes.length
          ? [backendNodeCatalogProvider(nodeCatalog)]
          : []
        : installations
          ? installations.map(backendProviderFromInstallation)
          : pluginError
            ? PLUGIN_PROVIDERS.filter((provider) => provider.bundled).map((provider) => ({
                ...provider,
                backendUnavailable: true,
              }))
            : []
    return source.filter((provider) => providerMatchesSubtype(provider, activeSubtype))
  })()

  const categoryCounts = (() => {
    const counts = new Map<PluginCategoryFilter, number>([['all', availableProviders.length]])
    for (const provider of availableProviders) {
      counts.set(provider.category, (counts.get(provider.category) ?? 0) + 1)
    }
    return counts
  })()

  const providers = (() => {
    const needle = query.trim().toLowerCase()
    return availableProviders.filter((provider) => {
      if (activeCategory !== 'all' && provider.category !== activeCategory) return false
      if (!needle) return true
      return `${provider.name} ${provider.author} ${provider.description} ${provider.tags.join(' ')}`
        .toLowerCase()
        .includes(needle)
    })
  })()


  const activeCategoryLabel = activeCategory === 'all'
    ? '全部插件'
    : pluginProviderCategoryLabel(activeCategory)
  const subtypeLabel: Record<PluginSubtype, string> = {
    source: '源库',
    template: '模板',
    tool: '工具',
    agent: 'Agent',
    trigger: '触发器',
    extension: '扩展',
  }
  const sectionTitle = `${subtypeLabel[activeSubtype]} · ${activeCategoryLabel}`
  const sectionDescription = activeSubtype === 'template'
    ? '从已登记的模板创建项目；模板创建会沿用当前工作区权限和项目生命周期。'
    : activeTab === 'capabilities'
      ? '查看插件注册的节点功能实现、运行绑定、输入输出与依赖状态；Studio 使用同一份后端能力目录。'
      : '管理当前工作区已安装的 Provider。未安装、未启用或缺少运行绑定的能力会明确显示为受阻。'

  const topTabs = (
    <div className="flex w-full flex-col gap-3">
      <div className="flex w-full flex-wrap items-center justify-between gap-3">
        <PluginPageTabs active={activeTab} onSelect={(tab) => updateRoute({ tab })} />
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="插件工作区"
            className="min-h-10 rounded-md border bg-background px-3 text-sm"
            value={workspaceId ?? ''}
            onChange={(event) => {
              const params = new URLSearchParams(searchParams.toString())
              if (event.target.value) params.set('workspace', event.target.value)
              else params.delete('workspace')
              router.push(`/plugins?${params.toString()}`, { scroll: false })
            }}
            disabled={workspaces.isLoading || !workspaces.data?.length}
          >
            <option value="">选择工作区</option>
            {workspaces.data?.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>{workspace.name}</option>
            ))}
          </select>
          <Button
            variant="outline"
            size="sm"
            className="min-h-10"
            onClick={() => setDifyImportOpen(true)}
            disabled={!workspaceId}
          >
            <Download aria-hidden="true" className="size-4" />
            安装插件包
          </Button>
        </div>
      </div>
      <PluginSubtypeTabs active={activeSubtype} onSelect={updateSubtype} />
    </div>
  )
  async function toggleInstallation(installation: BackendPluginInstallation) {
    if (!workspaceId) return
    try {
      await updatePluginInstallation(workspaceId, installation.id, {
        enabled: !installation.enabled,
      })
      await queryClient.invalidateQueries({
        queryKey: ['plugin-installations', workspaceId],
      })
      await queryClient.invalidateQueries({
        queryKey: [...NODE_CAPABILITY_CATALOG_QUERY_KEY, workspaceId],
      })
      setSelectedProvider(null)
      toast.success(installation.enabled ? '插件已停用' : '插件已启用')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '插件状态更新失败')
    }
  }

  return (
    <PageContainer
      eyebrow="Plugins"
      title="插件中心"
      description="管理已经接入的能力包，并按需安装新的 Provider。"
      tabs={topTabs}
    >
      <div className="grid min-w-0 gap-6 lg:grid-cols-[10rem_minmax(0,1fr)]">
        <aside className="min-w-0">
          <nav aria-label="插件分类" className="flex gap-1 overflow-x-auto lg:sticky lg:top-4 lg:flex-col">
            {PLUGIN_PROVIDER_CATEGORIES.map((item) => {
              const selected = activeCategory === item.key
              return (
                <button
                  key={item.key}
                  type="button"
                  aria-current={selected ? 'page' : undefined}
                  onClick={() => updateRoute({ category: item.key })}
                  className={cn(
                    'flex min-h-10 shrink-0 items-center justify-between gap-4 rounded-xs px-3 text-left text-xs font-medium transition-colors',
                    selected
                      ? 'bg-primary-500/10 text-primary-400'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                  )}
                >
                  <span>{item.label}</span>
                  <span
                    className={cn(
                      'font-mono text-3xs tabular-nums',
                      selected ? 'text-primary-400/80' : 'text-muted-foreground/70',
                    )}
                  >
                    {categoryCounts.get(item.key) ?? 0}
                  </span>
                </button>
              )
            })}
          </nav>
        </aside>

        <main className="min-w-0">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-base font-semibold">{pluginError ? '插件能力目录' : sectionTitle}</h2>
              <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">{sectionDescription}</p>
            </div>
            <div className="relative w-full sm:w-72">
              <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="min-h-10 pl-9"
                placeholder={activeTab === 'capabilities' ? '搜索节点能力或 Provider' : '搜索插件'}
                aria-label={activeTab === 'capabilities' ? '搜索节点能力或 Provider' : '搜索插件'}
              />
            </div>
          </div>

          {workspaceError || pluginError || catalogError || capabilityError || opencliError ? (
            <div className="mb-4 flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs">
              <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning" />
              <div>
                <div className="font-medium">
                  {workspaceError
                    ? '工作区访问不可用'
                    : pluginError
                      ? '后端插件注册表暂时不可用'
                      : '部分 Provider 状态暂时不可用'}
                </div>
                <p className="mt-1 text-muted-foreground">
                  {workspaceError ?? pluginError ?? catalogError ?? capabilityError ?? opencliError}
                </p>
                {pluginError ? (
                  <p className="mt-1 text-muted-foreground">
                    当前仅显示不可用的前端目录占位，不会把它们标记成“已安装”。
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          {activeSubtype === 'template' ? (
            <TemplateCatalog workspaceId={workspaceId} />
          ) : (
            <>
              {activeTab === 'capabilities' && nodeCatalog ? (
                <section aria-label="后端节点能力摘要" className="mb-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  <CapabilityMetric label="节点总数" value={nodeCatalog.summary.total} detail={`${nodeCatalog.categories.length} 个分类`} />
                  <CapabilityMetric label="可运行" value={nodeCatalogCounts.runnable} detail="已验证运行绑定" />
                  <CapabilityMetric label="组合能力" value={nodeCatalogCounts.composed} detail="预览，不计入可运行" />
                  <CapabilityMetric label="待补齐" value={nodeCatalogCounts.pending} detail="受阻或需要插件" />
                </section>
              ) : null}

              {(pluginLoading || capabilityLoading || opencliLoading) &&
              (installations === null || capabilities === null) ? (
                <div className="grid min-h-48 place-items-center rounded-md border border-dashed">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                    正在读取插件状态
                  </div>
                </div>
              ) : providers.length ? (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {providers.map((provider) => {
                    const providerNodes = providerNodeViews(
                      provider,
                      nodeCatalog,
                      nodesById,
                      activeCategory,
                    )
                    const state = providerState(provider, providerNodes, summary.adapterCount)
                    const capabilityCount = provider.id === 'opencli'
                      ? summary.adapterCount
                      : providerNodes.length
                    const metric = provider.id === 'opencli'
                      ? `${capabilityCount} 个网站`
                      : capabilityCount > 0
                        ? `${capabilityCount} 项能力`
                        : '查看详情'
                    return (
                      <ProviderCard
                        key={provider.installation?.id ?? provider.id}
                        provider={provider}
                        state={state}
                        metric={metric}
                        onOpen={() => {
                          if (provider.id === 'opencli') {
                            router.push(
                              workspaceId
                                ? `/plugins/opencli?workspace=${encodeURIComponent(workspaceId)}`
                                : '/plugins/opencli',
                            )
                            return
                          }
                          setSelectedProvider(provider)
                        }}
                      />
                    )
                  })}
                </div>
              ) : (
                <EmptyState
                  title={pluginError ? '插件注册表不可用' : `${activeCategoryLabel}中没有匹配项`}
                  description={query
                    ? '清除搜索词或切换分类后再试。'
                    : '切换到其他分类，或通过“安装插件包”接入当前工作区的 Provider。'}
                />
              )}
            </>
          )}
        </main>
      </div>

      <Sheet
        open={selectedProvider !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedProvider(null)
        }}
      >
        {selectedProvider ? (
          <ProviderDetails
            provider={selectedProvider}
            nodes={providerNodeViews(
              selectedProvider,
              nodeCatalog,
              nodesById,
              activeCategory,
            )}
            workspaceId={workspaceId}
            state={providerState(
              selectedProvider,
              providerNodeViews(
                selectedProvider,
                nodeCatalog,
                nodesById,
                activeCategory,
              ),
              summary.adapterCount,
            )}
            opencliAdapterCount={summary.adapterCount}
            onImportRss={() => {
              setSelectedProvider(null)
              setRssImportOpen(true)
            }}
            onToggleInstallation={toggleInstallation}
          />
        ) : null}
      </Sheet>

      <RssCatalogImportDialog open={rssImportOpen} onOpenChange={setRssImportOpen} />
      <DifyPackageImportDialog
        open={difyImportOpen}
        onOpenChange={setDifyImportOpen}
        workspaceId={workspaceId}
        onImported={(installation) => {
          setSelectedProvider(backendProviderFromInstallation(installation))
          updateSubtype(pluginSubtypeForInstallation(installation))
        }}
      />

    </PageContainer>
  )
}
function backendProviderFromInstallation(
  installation: BackendPluginInstallation,
): RegistryPluginProvider {
  const fallbackId = BUNDLED_PROVIDER_ID_BY_KEY[installation.providerKey]
  const fallback = fallbackId
    ? PLUGIN_PROVIDERS.find((provider) => provider.id === fallbackId)
    : undefined
  const category = pluginCategoryFromInstallation(installation)
  const label = installation.labels.zh_Hans ?? installation.labels.en_US ?? installation.name
  const description = installation.descriptions.zh_Hans
    ?? installation.descriptions.en_US
    ?? fallback?.description
    ?? 'Dify 插件能力声明。'
  return {
    id: fallback?.id ?? installation.id,
    name: label,
    author: installation.author,
    category: fallback?.category ?? category,
    description,
    icon: fallback?.icon ?? pluginIconFromCategory(category),
    nodeIds: fallback?.nodeIds ?? [],
    tags: [
      ...new Set([
        ...(fallback?.tags ?? []),
        installation.providerKey,
        installation.version,
        ...installation.pluginTypes,
      ]),
    ],
    bundled: installation.bundled,
    installation,
  }
}

function backendNodeCatalogProvider(
  catalog: BackendNodeCapabilityCatalog,
): RegistryPluginProvider {
  const catalogCategories = [
    ...new Set(catalog.nodes.map(backendNodeProviderCategory)),
  ]
  return {
    id: 'backend-node-capabilities',
    name: 'OpenCLI 节点能力',
    author: catalog.authority === 'backend' ? 'OpenCLI Backend' : catalog.authority,
    category: 'bundle',
    description: '后端统一登记的原生、组合、插件与兼容节点；Plugin Center 和 Studio 使用同一份目录。',
    icon: 'puzzle',
    nodeIds: catalog.nodes.map((node) => node.id),
    tags: [
      'node',
      'capability',
      'dify',
      ...catalog.categories.map((category) => category.label),
      ...catalog.nodes.flatMap((node) => [node.id, node.label]),
    ],
    bundled: true,
    nodeCatalog: true,
    catalogCategories,
  }
}

function providerNodeViews(
  provider: RegistryPluginProvider,
  catalog: BackendNodeCapabilityCatalog | null,
  legacyNodesById: Map<string, WorkflowNodeCatalogItem>,
  activeCategory: PluginCategoryFilter = 'all',
): ProviderNodeView[] {
  const referencedIds = new Set(provider.nodeIds)
  const installation = provider.installation
  for (const definition of installation?.nodeDefinitions ?? []) referencedIds.add(definition.id)
  for (const capability of installation?.capabilities ?? []) {
    if (capability.runtimeAdapterId) referencedIds.add(capability.runtimeAdapterId)
  }

  const backendNodes = (catalog?.nodes ?? []).filter((node) => {
    if (provider.nodeCatalog) {
      return activeCategory === 'all'
        || backendNodeProviderCategory(node) === activeCategory
    }
    if (referencedIds.has(node.id)) return true
    return installation ? node.provider === installation.providerKey : false
  })
  if (backendNodes.length > 0) return backendNodes.map(providerNodeViewFromBackend)

  return [...referencedIds].flatMap((id) => {
    const node = legacyNodesById.get(id)
    return node ? [providerNodeViewFromLegacy(node)] : []
  })
}

function backendNodeProviderCategory(
  node: BackendNodeCapabilityDefinition,
): PluginProviderCategory {
  if (node.category === 'ai') return 'model'
  if (node.category === 'agent') return 'agent'
  if (node.category === 'tool') return 'tool'
  if (node.category === 'plugin' && node.kind === 'source') return 'datasource'
  if (node.category === 'plugin' && node.kind === 'schedule') return 'trigger'
  return 'extension'
}

function providerNodeViewFromBackend(node: BackendNodeCapabilityDefinition): ProviderNodeView {
  const runtimeReady = backendNodeCapabilityIsRunnable(node)
  return {
    id: node.id,
    label: node.label,
    description: node.description,
    category: node.category,
    readiness: node.readiness === 'runnable' && !runtimeReady ? 'blocked' : node.readiness,
    runtimeReady,
    missing: node.missing,
  }
}

function providerNodeViewFromLegacy(node: WorkflowNodeCatalogItem): ProviderNodeView {
  const status = node.runtimeCapability?.status
  const runtimeReady = status === 'runnable' && node.runtimeCapability?.backendAvailable === true
  return {
    id: node.id,
    label: node.label,
    description: node.description,
    category: node.category,
    readiness: runtimeReady ? 'runnable' : 'blocked',
    runtimeReady,
    missing: node.runtimeCapability?.missing ?? [],
  }
}

function nodeCapabilityIsUsable(node: ProviderNodeView): boolean {
  return node.runtimeReady
}

function pluginCategoryFromInstallation(
  installation: BackendPluginInstallation,
): PluginProviderCategory {
  const types = new Set(installation.pluginTypes)
  if (types.has('model')) return 'model'
  if (types.has('datasource')) return 'datasource'
  if (types.has('trigger')) return 'trigger'
  if (types.has('agent_strategy')) return 'agent'
  if (types.has('endpoint')) return 'extension'
  return 'tool'
}
function pluginSubtypeForInstallation(
  installation: BackendPluginInstallation,
): PluginSubtype {
  const types = new Set(installation.pluginTypes)
  if (types.has('datasource')) return 'source'
  if (types.has('agent_strategy')) return 'agent'
  if (types.has('trigger')) return 'trigger'
  if (types.has('endpoint')) return 'extension'
  return 'tool'
}

function pluginIconFromCategory(category: PluginProviderCategory): PluginProviderIcon {
  if (category === 'model') return 'brain'
  if (category === 'datasource') return 'database'
  if (category === 'agent') return 'bot'
  if (category === 'trigger') return 'clock'
  if (category === 'extension') return 'puzzle'
  return 'wrench'
}

'use client'

import Link from 'next/link'
import { Bot, LogOut, Search, Settings } from 'lucide-react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Fragment, Suspense } from 'react'

import { useAuth } from '@/components/auth/auth-provider'
import { ROUTE_LABELS } from '@/lib/navigation'
import { navigationBreadcrumbs } from '@/lib/navigation-breadcrumbs'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb'
import { Kbd, KbdGroup } from '@/components/ui/kbd'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { ThemeToggle } from '@/components/shell/theme-toggle'

function HeaderBreadcrumbs() {
  const pathname = usePathname()
  const params = useSearchParams()
  const crumbs = navigationBreadcrumbs(pathname, new URLSearchParams(params.toString()), ROUTE_LABELS)

  return (
    <Breadcrumb className="min-w-0 flex-1 overflow-hidden">
      <BreadcrumbList className="flex-nowrap overflow-x-auto whitespace-nowrap">
        {crumbs.map((crumb, index) => (
          <Fragment key={`${crumb.label}-${index}`}>
            {index > 0 ? <BreadcrumbSeparator /> : null}
            <BreadcrumbItem className="shrink-0">
              {crumb.href ? (
                <BreadcrumbLink render={<Link href={crumb.href} />}>{crumb.label}</BreadcrumbLink>
              ) : (
                <BreadcrumbPage>{crumb.label}</BreadcrumbPage>
              )}
            </BreadcrumbItem>
          </Fragment>
        ))}
      </BreadcrumbList>
    </Breadcrumb>
  )
}
export function AppHeader({
  onOpenAgent,
  onOpenCommand,
}: {
  onOpenAgent?: () => void
  onOpenCommand?: () => void
}) {
  const router = useRouter()
  const { identity, signOut } = useAuth()
  const displayName =
    identity?.name || identity?.username || identity?.email || identity?.subject || 'User'
  const accountLabel =
    identity?.email ||
    identity?.username ||
    (identity?.is_platform_admin ? 'Platform Admin' : identity?.auth_method)
  const avatarUrl =
    identity?.picture?.startsWith('https://') || identity?.picture?.startsWith('http://')
      ? identity.picture
      : undefined
  const initials = displayName.slice(0, 2).toUpperCase()

  async function handleSignOut() {
    await signOut()
    router.replace('/login')
  }

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b bg-background/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <SidebarTrigger />
      <Separator orientation="vertical" className="mr-1 h-5" />
      <Suspense fallback={<span className="flex-1 text-sm text-muted-foreground">概览</span>}>
        <HeaderBreadcrumbs />
      </Suspense>

      <div className="ml-auto flex items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="hidden gap-2 sm:flex"
          onClick={onOpenAgent}
        >
          <Bot />
          <span>Ask Alice</span>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="sm:hidden"
          aria-label="打开 Ask Alice 主对话"
          onClick={onOpenAgent}
        >
          <Bot />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="hidden gap-2 text-muted-foreground sm:flex"
          onClick={onOpenCommand}
        >
          <Search />
          <span>搜索…</span>
          <KbdGroup className="ml-2">
            <Kbd>⌘</Kbd>
            <Kbd>K</Kbd>
          </KbdGroup>
        </Button>
        <Button variant="ghost" size="icon" className="sm:hidden" aria-label="搜索" onClick={onOpenCommand}>
          <Search />
        </Button>
        <ThemeToggle />
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon" className="rounded-full" aria-label="账号菜单" />
            }
          >
            <Avatar className="size-7">
              {avatarUrl ? <AvatarImage src={avatarUrl} alt={`${displayName} 头像`} /> : null}
              <AvatarFallback className="text-[10px]">{initials}</AvatarFallback>
            </Avatar>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuGroup>
              <DropdownMenuLabel className="font-normal">
                <span className="block truncate text-sm font-medium text-foreground">{displayName}</span>
                <span className="block truncate text-xs">{accountLabel}</span>
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuItem onClick={() => router.push('/settings')}>
              <Settings />
              账户设置
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={handleSignOut}>
              <LogOut />
              退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}

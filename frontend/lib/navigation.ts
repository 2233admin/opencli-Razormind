import {
  Activity,
  Blocks,
  Database,
  LayoutDashboard,
  PanelsTopLeft,
  ShieldCheck,
  Workflow,
  type LucideIcon,
} from 'lucide-react'

export type NavItem = {
  href: string
  label: string
  icon: LucideIcon
  /** extra path prefixes that keep this item highlighted (sibling tab routes) */
  match?: string[]
}
export type NavGroup = {
  label: string | null
  items: NavItem[]
}

/**
 * Action-oriented IA: orient in the workbench, build automations, observe
 * execution, then manage the platform.
 * Related resource routes remain available through each destination's tabs.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: '工作台',
    items: [
      { href: '/dashboard', label: '概览', icon: LayoutDashboard },
      {
        href: '/inbox',
        label: '任务与通知',
        icon: Activity,
        match: ['/inbox', '/tasks', '/notifications'],
      },
    ],
  },
  {
    label: '构建',
    items: [
      { href: '/studio', label: '项目', icon: PanelsTopLeft, match: ['/studio', '/canvas'] },
      { href: '/plugins', label: '插件中心', icon: Blocks },
      {
        href: '/schedules',
        label: '自动化与 Agent',
        icon: Workflow,
        match: ['/schedules', '/agents', '/skills'],
      },
    ],
  },
  {
    label: '运行与数据',
    items: [
      { href: '/records', label: '成果与数据', icon: Database },
      {
        href: '/nodes',
        label: '执行资源',
        icon: Activity,
        match: ['/nodes', '/workers'],
      },
    ],
  },
  {
    label: '管理',
    items: [
      {
        href: '/providers',
        label: '模型与连接',
        icon: ShieldCheck,
        match: ['/providers', '/control/actions', '/control'],
      },
    ],
  },
]

/** Labels for every route (incl. tab siblings) used by breadcrumbs. */
export const ROUTE_LABELS: Record<string, string> = {
  '/dashboard': '概览',
  '/inbox': '任务与通知',
  '/studio': '项目',
  '/studio/workflow': '工作流编排',
  '/canvas': '节点工作流（兼容入口）',
  '/plugins': '插件中心',
  '/sources': '数据（兼容入口）',
  '/schedules': '触发与调度',
  '/tasks': '工作项',
  '/records': '成果与数据',
  '/notifications': '通知',
  '/agents': '智能体',
  '/skills': '技能',
  '/providers': '模型与连接',
  '/nodes': '执行资源',
  '/workers': 'Worker',
  '/control/actions': '控制与审计',
  '/control': '控制中心',
}

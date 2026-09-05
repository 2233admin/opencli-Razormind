import { BrowserAccountsPanel } from '@/components/browsers/browser-accounts-panel'
import { PageContainer } from '@/components/shell/page-container'

export default function BrowserAccountsPage() {
  return <PageContainer title="账号登录" description="添加网站，直接扫码或输入登录，独立保存每个账号的会话。"><BrowserAccountsPanel /></PageContainer>
}

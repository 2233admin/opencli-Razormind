import { ProviderManagementPanel } from '@/components/providers/provider-management-panel'
import { RssGeneratorProviderPanel } from '@/components/providers/rss-generator-provider-panel'
import { FeishuBitableConnectionPanel } from '@/components/providers/feishu-bitable-connection-panel'
import { FeishuConnectorInstallationPanel } from '@/components/providers/feishu-connector-installation-panel'

export default function ProviderCatalogPage() {
  return (
    <div className="flex flex-col gap-8">
      <ProviderManagementPanel />
      <FeishuBitableConnectionPanel />
      <FeishuConnectorInstallationPanel />
      <RssGeneratorProviderPanel />
    </div>
  )
}

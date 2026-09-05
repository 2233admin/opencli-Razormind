import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')

export default {
  allowedDevOrigins: ['127.0.0.1'],
  webpack(config) {
    config.resolve.alias['@'] = frontendRoot
    return config
  },
}

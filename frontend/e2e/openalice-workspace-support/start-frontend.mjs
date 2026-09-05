import { existsSync } from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const supportRoot = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(supportRoot, '..', '..')
const nextBin = path.join(frontendRoot, 'node_modules', 'next', 'dist', 'bin', 'next')
const standaloneServer = path.join(frontendRoot, '.next', 'standalone', 'server.js')
const backendUrl = process.env.BACKEND_URL ?? 'http://127.0.0.1:8048'
const frontendPort = process.env.OPENALICE_FRONTEND_PORT ?? '8049'

let command
let args
if (existsSync(nextBin)) {
  command = process.execPath
  // The dependency junction is intentionally outside this worktree. Use the
  // webpack dev path so Next does not reject that read-only test dependency
  // link as a Turbopack project-root escape.
  args = [nextBin, 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', frontendPort]
} else if (existsSync(standaloneServer)) {
  command = process.execPath
  args = [path.join(frontendRoot, 'scripts', 'start-standalone.mjs')]
} else {
  console.error('OpenAlice frontend requires either frontend/node_modules/next or a built .next/standalone/server.js')
  process.exit(1)
}

const child = spawn(command, args, {
  cwd: frontendRoot,
  env: {
    ...process.env,
    BACKEND_URL: backendUrl,
    HOSTNAME: '127.0.0.1',
    PORT: frontendPort,
  },
  stdio: 'inherit',
})

const stop = () => {
  if (!child.killed) child.kill()
}
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
process.on('exit', stop)
child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)))

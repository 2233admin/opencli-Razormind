import { existsSync, mkdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const supportRoot = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(supportRoot, '..', '..')
const repositoryRoot = path.resolve(frontendRoot, '..')
const dbRoot = path.join(supportRoot, '.runtime')
const dbPath = path.join(dbRoot, 'openalice-workspace.sqlite3')
mkdirSync(dbRoot, { recursive: true })
rmSync(dbPath, { force: true })

const pythonCandidates = [
  process.env.OPENALICE_PYTHON,
  path.join(repositoryRoot, '.venv', 'Scripts', 'python.exe'),
  path.join(repositoryRoot, '.venv', 'bin', 'python'),
  'python',
].filter(Boolean)
const python = pythonCandidates.find((candidate) => candidate === 'python' || existsSync(candidate)) ?? 'python'
const runner = path.join(frontendRoot, 'e2e', 'openalice-workspace-support', 'run-backend.py')
const port = process.env.OPENALICE_BACKEND_PORT ?? '8048'
const child = spawn(python, [runner, '--db', dbPath, '--port', port], {
  cwd: repositoryRoot,
  env: {
    ...process.env,
    PYTHONPATH: [repositoryRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
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

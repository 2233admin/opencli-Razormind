import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const args = process.argv.slice(2)
const envFileArg = args.find((arg) => arg.startsWith('--env-file='))
const profilesArg = args.find((arg) => arg.startsWith('--profiles='))
const envFile = path.resolve(root, envFileArg?.slice('--env-file='.length) || '.env')

function parseEnv(file) {
  if (!existsSync(file)) return {}
  const values = {}
  for (const rawLine of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/)
    if (!match) continue
    let value = match[2].trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    values[match[1]] = value
  }
  return values
}

const fileEnv = parseEnv(envFile)
const config = { ...fileEnv, ...process.env }
const inferredProfiles = ['core']
if (config.TASK_EXECUTOR === 'celery') inferredProfiles.push('celery')
if (config.COLLECTION_MODE === 'agent') inferredProfiles.push('agent')
if (config.CHROME_SUFFIX) inferredProfiles.push('embedded-chrome')
if (config.INVOKEAI_ENABLED === 'true') inferredProfiles.push('image-studio')
const explicitProfiles = profilesArg
  ? profilesArg.slice('--profiles='.length).split(',').map((item) => item.trim()).filter(Boolean)
  : []
const requestedProfiles = [...inferredProfiles, ...explicitProfiles]
const profiles = [...new Set(['core', ...requestedProfiles])]
const errors = []
const notes = []

function value(name) {
  return (config[name] || '').trim()
}

function requireValue(name, profile) {
  const current = value(name)
  if (!current) errors.push(`[${profile}] ${name} is required`)
  return current
}

function requireOne(names, profile) {
  if (!names.some((name) => value(name))) {
    errors.push(`[${profile}] configure at least one of: ${names.join(', ')}`)
  }
}

function requireExact(name, expected, profile) {
  const current = requireValue(name, profile)
  if (current && current !== expected) errors.push(`[${profile}] ${name} must be ${expected}`)
}

function checkCommand(command, commandArgs, label) {
  try {
    return execFileSync(command, commandArgs, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch {
    errors.push(`[tools] ${label} is unavailable`)
    return ''
  }
}

const rules = {
  core() {
    if (!existsSync(envFile)) errors.push(`[core] environment file not found: ${envFile}`)
    for (const name of ['API_AUTH_TOKEN', 'BOOTSTRAP_ADMIN_TOKEN', 'SECRET_KEY', 'CREDENTIAL_ENCRYPTION_KEY', 'DATABASE_URL']) {
      requireValue(name, 'core')
    }
    if (['change-me', 'change-me-in-production'].includes(value('SECRET_KEY'))) {
      errors.push('[core] SECRET_KEY still uses a placeholder')
    }
    const suffix = value('CHROME_SUFFIX')
    if (suffix && suffix !== '-chrome') errors.push('[core] CHROME_SUFFIX must be empty or -chrome')
  },
  celery() {
    requireExact('TASK_EXECUTOR', 'celery', 'celery')
    if (value('DATABASE_URL').includes('sqlite')) {
      errors.push('[celery] DATABASE_URL must use PostgreSQL; SQLite is unsafe for distributed workers')
    }
  },
  postgres() {
    if (!requireValue('DATABASE_URL', 'postgres').startsWith('postgresql')) {
      errors.push('[postgres] DATABASE_URL must use a PostgreSQL driver')
    }
    for (const name of ['POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD']) requireValue(name, 'postgres')
  },
  agent() {
    requireValue('CENTRAL_API_URL', 'agent')
    requireValue('API_AUTH_TOKEN', 'agent')
    const registration = value('AGENT_REGISTER') || 'http'
    if (!['http', 'ws'].includes(registration)) errors.push('[agent] AGENT_REGISTER must be http or ws')
    if (registration === 'http') requireValue('AGENT_ADVERTISE_URL', 'agent')
  },
  'embedded-chrome'() {
    requireExact('CHROME_SUFFIX', '-chrome', 'embedded-chrome')
  },
  ai() {
    requireOne(['OPENAI_API_KEY', 'ANTHROPIC_API_KEY'], 'ai')
  },
  dify() {
    notes.push('[dify] uses the internal Graphon runtime URL unless DIFY_GRAPHON_RUNTIME_URL overrides it')
  },
  kats() {
    notes.push('[kats] uses the internal Kats runtime URL unless KATS_RUNTIME_URL overrides it')
  },
  'image-studio'() {
    requireExact('INVOKEAI_ENABLED', 'true', 'image-studio')
    const image = requireValue('INVOKEAI_ATTESTED_IMAGE', 'image-studio')
    if (image && (!image.includes('@sha256:') || image.includes('0000000000000000'))) {
      errors.push('[image-studio] INVOKEAI_ATTESTED_IMAGE must be an attested digest-pinned image')
    }
    requireValue('INVOKEAI_API_TOKEN', 'image-studio')
  },
}

for (const profile of profiles) {
  if (!rules[profile]) errors.push(`[profiles] unknown profile: ${profile}`)
  else rules[profile]()
}

const nodeVersion = process.versions.node
const expectedNode = existsSync(path.join(root, '.nvmrc')) ? readFileSync(path.join(root, '.nvmrc'), 'utf8').trim() : ''
if (expectedNode && nodeVersion.split('.')[0] !== expectedNode.split('.')[0]) {
  errors.push(`[tools] Node ${expectedNode}.x required; active version is ${nodeVersion}`)
}
checkCommand('uv', ['--version'], 'uv')
checkCommand('uv', ['lock', '--check'], 'uv lock')
checkCommand('docker', ['compose', '--env-file', envFile, '-f', 'docker-compose.yml', '-f', 'docker-compose.build.yml', 'config', '--quiet'], 'Docker Compose configuration')

for (const note of notes) console.log(`NOTE ${note}`)
if (errors.length) {
  for (const error of errors) console.error(`ERROR ${error}`)
  console.error(`Environment check failed for profiles: ${profiles.join(', ')}`)
  process.exit(1)
}

console.log(`Environment ready: ${profiles.join(', ')}`)

import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'

const root = process.cwd()
const script = path.join(root, 'scripts', 'dev-environment.mjs')
const validCore = `API_AUTH_TOKEN=a\nBOOTSTRAP_ADMIN_TOKEN=b\nSECRET_KEY=c\nCREDENTIAL_ENCRYPTION_KEY=d\nDATABASE_URL=sqlite+aiosqlite:///test.db\nCHROME_SUFFIX=\n`

function envFile(contents) {
  const directory = mkdtempSync(path.join(tmpdir(), 'opencli-env-doctor-'))
  const file = path.join(directory, '.env')
  writeFileSync(file, contents)
  return file
}

test('accepts the default core profile with an empty Chrome suffix', () => {
  const output = execFileSync(process.execPath, [script, `--env-file=${envFile(validCore)}`], {
    cwd: root,
    encoding: 'utf8',
  })
  assert.match(output, /Environment ready: core/)
})

test('rejects embedded Chrome without the image suffix', () => {
  const result = spawnSync(process.execPath, [script, `--env-file=${envFile(validCore)}`, '--profiles=embedded-chrome'], {
    cwd: root,
    encoding: 'utf8',
  })
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /CHROME_SUFFIX (?:is required|must be -chrome)/)
})

test('rejects HTTP agent registration without an advertised URL', () => {
  const file = envFile(`${validCore}CENTRAL_API_URL=http://center:8031\nAGENT_REGISTER=http\n`)
  const result = spawnSync(process.execPath, [script, `--env-file=${file}`, '--profiles=agent'], {
    cwd: root,
    encoding: 'utf8',
  })
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /AGENT_ADVERTISE_URL is required/)
})

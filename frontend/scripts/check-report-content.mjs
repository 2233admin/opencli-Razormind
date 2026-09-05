import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import test from "node:test"

const root = new URL("../", import.meta.url)

async function source(path) {
  return readFile(new URL(path, root), "utf8")
}

test("report content exposes every supported preview state and kind", async () => {
  const contract = await source("lib/artifacts/report-content.ts")
  const component = await source("components/artifacts/report-content-view.tsx")

  for (const kind of ["markdown", "html", "text", "json", "code", "table"]) {
    assert.match(contract, new RegExp(`['"]${kind}['"]`))
  }
  for (const state of ["loading", "empty", "error", "unsupported"]) {
    assert.match(component, new RegExp(`data-report-state=\\\"${state}\\\"|${state}`))
  }
  assert.match(component, /export type ReportContentViewProps/)
})
test("markdown preview sanitizes before adding copy controls", async () => {
  const contract = await source("lib/artifacts/report-content.ts")
  const component = await source("components/artifacts/report-content-view.tsx")

  assert.match(contract, /purifier\.sanitize\(parsed/)
  assert.match(contract, /restrictMarkdownLinks\(sanitized\)/)
  assert.match(contract, /data-copy-report-code/)
  assert.match(component, /navigator\.clipboard\.writeText/)
})

test("HTML preview is isolated and has no active resource policy", async () => {
  const contract = await source("lib/artifacts/report-content.ts")
  const component = await source("components/artifacts/report-content-view.tsx")

  assert.match(component, /sandbox=\"\"/)
  assert.match(component, /referrerPolicy=\"no-referrer\"/)
  assert.match(contract, /script-src 'none'/)
  assert.match(contract, /connect-src 'none'/)
  assert.match(contract, /FORBID_TAGS: \[\.\.\.HTML_FORBIDDEN_TAGS\]/)
  assert.match(contract, /startsWith\(\"data:\"\)/)
})

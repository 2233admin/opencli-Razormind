import { ReportContentView } from '../../../components/artifacts/report-content-view'

const markdown = `# Hostile report

[Source](https://example.com/source) [Unsafe](javascript:alert(1))

| Name | Value |
| --- | --- |
| wide | ${'wide-cell-'.repeat(35)} |

\`\`\`javascript
const answer = 42
\`\`\`

<style>body { display:none }</style>
<form action="https://outside.invalid/form"><input name="secret" /></form>
<script>window.reportScriptRan = true</script>
<img src="https://outside.invalid/image" onerror="window.reportScriptRan=true" />
<audio src="https://outside.invalid/audio" autoplay></audio>
<video poster="https://outside.invalid/poster" src="https://outside.invalid/video" autoplay></video>
<svg><image href="https://outside.invalid/svg" /></svg>
`

const html = `<h1>Safe report</h1>
<script>parent.reportScriptRan = true; top.location='https://outside.invalid/escape'</script>
<style>@import url('https://outside.invalid/css'); body{background-image:url('https://outside.invalid/background')}</style>
<form action="https://outside.invalid/form"><input name="secret" /></form>
<a href="https://outside.invalid/navigation">Untrusted navigation</a>
<img src="https://outside.invalid/image" onerror="parent.reportScriptRan = true" />
<iframe src="https://outside.invalid/frame"></iframe>`

export default function Page() {
  return (
    <main style={{ width: '100%', maxWidth: 960, padding: 16, margin: 'auto' }}>
      <section data-testid="report-content-markdown"><ReportContentView content={markdown} mediaType="text/markdown" /></section>
      <section data-testid="report-content-html"><ReportContentView content={html} mediaType="text/html" /></section>
      <section data-testid="report-content-table"><ReportContentView content={[{ name: 'first', count: 1 }, { count: 2, name: 'second', note: 'new column' }]} mediaType="application/vnd.openalice.table+json" /></section>
      <section data-testid="report-content-csv"><ReportContentView content={'id,note\n001,"comma, tab\t and\nnewline"'} mediaType="text/csv" /></section>
      <section data-testid="report-content-tsv"><ReportContentView content={'id\tnote\n002\t"comma, kept"'} mediaType="text/tab-separated-values" /></section>
      <section data-testid="report-content-json"><ReportContentView content={{ answer: 42 }} mediaType="application/json" /></section>
      <ReportContentView state="loading" />
      <ReportContentView content="" />
      <ReportContentView state="error" errorMessage="Fixture read failed" />
      <ReportContentView content="bytes" mediaType="application/octet-stream" />
    </main>
  )
}

"use client"

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react"

import {
  coerceReportText,
  createIsolatedHtmlDocument,
  csvRows,
  inferReportContentKind,
  renderMarkdownHtml,
  type ReportContentKind,
  type ReportContentState,
} from "@/lib/artifacts/report-content"
import styles from "./report-content.module.css"

export type ReportContentViewProps = {
  content?: unknown
  mediaType?: string
  fileName?: string
  state?: ReportContentState
  errorMessage?: string
  className?: string
}

const stateCopy: Record<Exclude<ReportContentState, "ready">, string> = {
  loading: "Loading report…",
  empty: "This report has no content.",
  error: "The report could not be loaded.",
}

export function ReportContentView({
  content,
  mediaType,
  fileName,
  state = "ready",
  errorMessage,
  className = "",
}: ReportContentViewProps) {
  const [browserReady, setBrowserReady] = useState(false)
  useEffect(() => setBrowserReady(true), [])

  if (state !== "ready") {
    return (
      <div
        className={`rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground ${className}`}
        data-report-state={state}
        role={state === "error" ? "alert" : "status"}
      >
        {state === "error" && errorMessage ? errorMessage : stateCopy[state]}
      </div>
    )
  }

  if (content === null || content === undefined || content === "") {
    return (
      <div
        className={`rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground ${className}`}
        data-report-state="empty"
        role="status"
      >
        {stateCopy.empty}
      </div>
    )
  }

  const kind = inferReportContentKind(mediaType, fileName)
  if (kind === "unsupported") {
    return (
      <div
        className={`rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground ${className}`}
        data-report-state="unsupported"
        role="status"
      >
        This report type is not supported for preview.
      </div>
    )
  }

  return (
    <ReadyReportContent
      browserReady={browserReady}
      content={content}
      kind={kind}
      className={className}
    />
  )
}

function ReadyReportContent({
  content,
  kind,
  className,
  browserReady,
}: {
  content: unknown
  kind: ReportContentKind
  className: string
  browserReady: boolean
}) {
  if (kind === "html") {
    return (
      <HtmlReportPreview
        browserReady={browserReady}
        content={coerceReportText(content)}
        className={className}
      />
    )
  }
  if (kind === "markdown") {
    return (
      <MarkdownReportPreview
        browserReady={browserReady}
        content={coerceReportText(content)}
        className={className}
      />
    )
  }
  if (kind === "json") return <JsonReportPreview content={content} className={className} />
  if (kind === "table") return <TableReportPreview content={content} className={className} />
  return <PlainReportPreview content={coerceReportText(content)} code={kind === "code"} className={className} />
}

function MarkdownReportPreview({
  content,
  className,
  browserReady,
}: {
  content: string
  className: string
  browserReady: boolean
}) {
  const contentRef = useRef<HTMLDivElement>(null)
  const html = useMemo(
    () => (browserReady ? renderMarkdownHtml(content) : ""),
    [browserReady, content],
  )

  const copyCode = useCallback(async (event: MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement
    const button = target.closest<HTMLButtonElement>("[data-copy-report-code]")
    if (!button) return
    const block = button.closest("[data-report-code-block]")
    const text = block?.querySelector("code")?.textContent || ""
    try {
      await navigator.clipboard.writeText(text)
      button.dataset.copied = "true"
      button.textContent = "✓ Copied"
      window.setTimeout(() => {
        if (!button.isConnected) return
        button.dataset.copied = "false"
        button.textContent = "⧉ Copy"
      }, 1600)
    } catch {
      button.textContent = "Copy unavailable"
    }
  }, [])

  if (!browserReady) {
    return <PlainReportPreview content={content} code={false} className={className} />
  }

  return (
    <div
      ref={contentRef}
      className={`${styles.markdown} report-markdown min-w-0 overflow-hidden rounded-lg border border-border bg-card p-4 text-sm leading-7 ${className}`}
      data-report-kind="markdown"
      onClick={copyCode}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}

function HtmlReportPreview({
  content,
  className,
  browserReady,
}: {
  content: string
  className: string
  browserReady: boolean
}) {
  const srcDoc = useMemo(
    () => (browserReady ? createIsolatedHtmlDocument(content) : ""),
    [browserReady, content],
  )
  return (
    <div className={`min-w-0 overflow-hidden rounded-lg border border-border bg-white ${className}`} data-report-kind="html">
      <iframe
        className="block h-[min(70vh,720px)] min-h-[360px] w-full border-0"
        referrerPolicy="no-referrer"
        sandbox=""
        srcDoc={srcDoc}
        title="HTML report preview"
      />
    </div>
  )
}

function JsonReportPreview({ content, className }: { content: unknown; className: string }) {
  return <PlainReportPreview content={coerceReportText(content)} code className={className} />
}

function PlainReportPreview({
  content,
  code,
  className,
}: {
  content: string
  code: boolean
  className: string
}) {
  return (
    <pre
      className={`min-w-0 max-w-full overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-card p-4 text-sm leading-6 ${code ? "font-mono" : ""} ${className}`}
      data-report-kind={code ? "code" : "text"}
    >
      {content}
    </pre>
  )
}

function TableReportPreview({ content, className }: { content: unknown; className: string }) {
  const contentArray = Array.isArray(content) ? content : []
  const objectRows = contentArray.filter(
        (row): row is Record<string, unknown> =>
          Boolean(row) && typeof row === "object" && !Array.isArray(row),
      )
  const isObjectTable = objectRows.length === contentArray.length && objectRows.length > 0
  const headers = isObjectTable
    ? Array.from(new Set(objectRows.flatMap((row) => Object.keys(row))))
    : []
  const rows = isObjectTable
    ? objectRows.map((row) => headers.map((header) => coerceReportText(row[header])))
    : contentArray.length > 0 || Array.isArray(content)
      ? contentArray.map((row) => (Array.isArray(row) ? row.map(coerceReportText) : [coerceReportText(row)]))
      : csvRows(coerceReportText(content))
  const columnHeaders = headers.length > 0
    ? headers
    : rows[0]?.map((_value, index) => `Column ${index + 1}`) || []
  const csvTable = !Array.isArray(content)

  return (
    <div className={`min-w-0 overflow-x-auto rounded-lg border border-border bg-card ${className}`} data-report-kind="table">
      <table className="min-w-full border-collapse text-left text-sm">
        <thead className="bg-muted/60">
          <tr>{columnHeaders.map((header) => <th className="border-b px-3 py-2 font-medium" key={header}>{header}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(csvTable ? 1 : 0).map((row, rowIndex) => (
            <tr className="align-top odd:bg-muted/20" key={`${rowIndex}-${row.join("|")}`}>
              {row.map((value, cellIndex) => <td className="border-b px-3 py-2 whitespace-pre-wrap" key={`${rowIndex}-${cellIndex}`}>{value}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

import DOMPurify from "dompurify"
import { Marked } from "marked"
import { markedHighlight } from "marked-highlight"
import hljs from "highlight.js/lib/common"
import * as XLSX from "xlsx"

export type ReportContentKind =
  | "markdown"
  | "html"
  | "text"
  | "json"
  | "code"
  | "table"

export type ReportContentState = "loading" | "empty" | "error" | "ready"

const MARKDOWN_COPY_ICON =
  '<span aria-hidden="true" class="report-copy-icon">⧉</span>'

const markdown = new Marked(
  markedHighlight({
    langPrefix: "hljs language-",
    highlight(code, language) {
      if (language && hljs.getLanguage(language)) {
        return hljs.highlight(code, { language }).value
      }
      return hljs.highlightAuto(code).value
    },
  }),
  { breaks: true, gfm: true },
)

const MARKDOWN_FORBIDDEN_TAGS = [
  "base",
  "embed",
  "form",
  "iframe",
  "input",
  "link",
  "meta",
  "object",
  "script",
  "select",
  "style",
  "textarea",
] as const

const HTML_FORBIDDEN_TAGS = [
  "base",
  "button",
  "embed",
  "form",
  "frame",
  "frameset",
  "iframe",
  "input",
  "link",
  "meta",
  "object",
  "script",
  "select",
  "textarea",
] as const

const REPORT_CSP = [
  "default-src 'none'",
  "base-uri 'none'",
  "connect-src 'none'",
  "font-src data:",
  "form-action 'none'",
  "frame-src 'none'",
  "img-src data:",
  "media-src data:",
  "object-src 'none'",
  "script-src 'none'",
  "style-src 'unsafe-inline'",
].join("; ")

const REPORT_BASE_STYLE = `
  html { background: #fff; color: #172033; }
  body { box-sizing: border-box; margin: 0; min-width: 0; padding: 24px;
    font-family: Inter, ui-sans-serif, system-ui, sans-serif; line-height: 1.55;
    overflow-wrap: anywhere; }
  *, *::before, *::after { box-sizing: inherit; }
  img, svg, video, canvas { max-width: 100%; height: auto; }
  table { max-width: 100%; border-collapse: collapse; }
  th, td { border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }
  pre { max-width: 100%; overflow: auto; }
  @media (max-width: 640px) { body { padding: 16px; } }
`

function browserPurifier(): ReturnType<typeof DOMPurify> {
  if (typeof window === "undefined") {
    throw new Error("report_content_browser_only")
  }
  return DOMPurify(window)
}

function escapeAttribute(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
}

function addMarkdownTableShells(html: string): string {
  return html
    .replaceAll("<table>", '<div class="report-table-shell"><table>')
    .replaceAll("</table>", "</table></div>")
}

function addMarkdownCodeActions(html: string): string {
  return html.replace(
    /<pre><code(?: class="hljs(?: language-([\w-]+))?")?>([\s\S]*?)<\/code><\/pre>/g,
    (_match, language: string | undefined, code: string) => {
      const label = language || "code"
      return `<div class="report-code-block" data-report-code-block><div class="report-code-header"><span>${escapeAttribute(label)}</span><button type="button" data-copy-report-code aria-label="Copy ${escapeAttribute(label)} code">${MARKDOWN_COPY_ICON} Copy</button></div><pre><code class="hljs${language ? ` language-${escapeAttribute(language)}` : ""}">${code}</code></pre></div>`
    },
  )
}

function restrictMarkdownLinks(html: string): string {
  const parser = new DOMParser()
  const document = parser.parseFromString(`<body>${html}</body>`, "text/html")
  for (const anchor of document.querySelectorAll("a")) {
    const href = (anchor.getAttribute("href") || "").trim()
    if (href.startsWith("#")) continue
    if (/^https?:\/\//i.test(href)) {
      anchor.setAttribute("target", "_blank")
      anchor.setAttribute("rel", "noopener noreferrer")
    } else {
      anchor.removeAttribute("href")
      anchor.setAttribute("aria-disabled", "true")
    }
  }
  for (const image of document.querySelectorAll("img")) {
    const src = (image.getAttribute("src") || "").trim().toLowerCase()
    if (!src.startsWith("data:")) image.removeAttribute("src")
    image.removeAttribute("srcset")
  }
  return document.body.innerHTML
}

export function coerceReportText(value: unknown): string {
  if (typeof value === "string") return value
  if (value === null || value === undefined) return ""
  return JSON.stringify(value, null, 2)
}

export function inferReportContentKind(
  mediaType: string | undefined,
  fileName: string | undefined = undefined,
): ReportContentKind | "unsupported" {
  const normalized = (mediaType || "").toLowerCase().split(";", 1)[0]
  if (normalized === "text/markdown" || normalized === "text/x-markdown") return "markdown"
  if (normalized === "text/html" || normalized === "application/xhtml+xml") return "html"
  if (normalized === "application/vnd.openalice.table+json") return "table"
  if (normalized === "application/json" || normalized.endsWith("+json")) return "json"
  if (normalized === "text/csv" || normalized === "text/tab-separated-values") return "table"
  if (normalized.startsWith("text/")) return "text"
  if (normalized.startsWith("application/") && normalized.includes("javascript")) return "code"

  const extension = (fileName || "").toLowerCase().split(".").pop()
  if (extension === "md" || extension === "markdown") return "markdown"
  if (extension === "html" || extension === "htm") return "html"
  if (extension === "json") return "json"
  if (extension === "csv" || extension === "tsv") return "table"
  if (["js", "jsx", "ts", "tsx", "py", "sql", "sh", "yaml", "yml"].includes(extension || "")) {
    return "code"
  }
  if (!mediaType || normalized === "text/plain") return "text"
  return "unsupported"
}

export function renderMarkdownHtml(source: string): string {
  const purifier = browserPurifier()
  const parsed = markdown.parse(source) as string
  const sanitized = purifier.sanitize(parsed, {
    FORBID_TAGS: [...MARKDOWN_FORBIDDEN_TAGS],
    FORBID_ATTR: ["style", "srcdoc"],
  })
  return addMarkdownTableShells(addMarkdownCodeActions(restrictMarkdownLinks(sanitized)))
}

function removeUnsafeHtmlResources(document: Document): void {
  for (const element of document.querySelectorAll<HTMLElement>(
    "[src], [srcset], [poster], [background], [href], [xlink\\:href]",
  )) {
    for (const attribute of ["src", "srcset", "poster", "background", "href", "xlink:href"]) {
      const value = element.getAttribute(attribute)
      if (value === null) continue
      if (attribute === "href" || attribute === "xlink:href") {
        if (!value.trim().startsWith("#")) element.removeAttribute(attribute)
      } else if (!value.trim().toLowerCase().startsWith("data:")) {
        element.removeAttribute(attribute)
      }
    }
  }
}

export function createIsolatedHtmlDocument(source: string): string {
  const purifier = browserPurifier()
  const sanitized = purifier.sanitize(source, {
    WHOLE_DOCUMENT: true,
    ADD_TAGS: ["style"],
    FORBID_TAGS: [...HTML_FORBIDDEN_TAGS],
    FORBID_ATTR: ["srcdoc"],
  })
  const document = new DOMParser().parseFromString(sanitized, "text/html")
  removeUnsafeHtmlResources(document)

  const csp = document.createElement("meta")
  csp.httpEquiv = "Content-Security-Policy"
  csp.content = REPORT_CSP
  const baseStyle = document.createElement("style")
  baseStyle.textContent = REPORT_BASE_STYLE
  document.head.replaceChildren(csp, baseStyle, ...Array.from(document.head.children))
  return `<!doctype html>\n${document.documentElement.outerHTML}`
}

export function csvRows(source: string): string[][] {
  const workbook = XLSX.read(source, {
    type: "string",
    raw: false,
    FS: source.includes("\t") ? "\t" : ",",
  })
  const firstSheet = workbook.Sheets[workbook.SheetNames[0] || ""]
  if (!firstSheet) return []
  return (XLSX.utils.sheet_to_json(firstSheet, {
    header: 1,
    defval: "",
    raw: false,
  }) as unknown[][]).map((row) => row.map(coerceReportText))
}

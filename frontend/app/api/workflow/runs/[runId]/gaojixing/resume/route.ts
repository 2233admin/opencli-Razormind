import { backendWorkflowRunsRoot, readWorkflowProxyScope } from "../../../../run-scope"
import { forwardedRequestAuthHeaders } from "@/lib/workflow/request-auth"

export const dynamic = "force-dynamic"

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8031"

export async function POST(req: Request, context: { params: Promise<{ runId: string }> }) {
  const { runId } = await context.params
  try {
    const scope = readWorkflowProxyScope(new URL(req.url))
    const body = await req.text()
    if (body.length > 1024) return Response.json({ success: false, message: "恢复请求过大" }, { status: 413 })
    let recoveryBody: string | undefined
    if (body.trim()) {
      let input: unknown
      try { input = JSON.parse(body) } catch { return Response.json({ success: false, message: "恢复请求格式无效" }, { status: 400 }) }
      if (!input || typeof input !== "object" || Array.isArray(input)
        || Object.keys(input).some((key) => key !== "expectedChatUrl")
        || !("expectedChatUrl" in input) || typeof input.expectedChatUrl !== "string"
        || !/^https:\/\/www\.doubao\.com\/chat\/\d+$/.test(input.expectedChatUrl)) {
        return Response.json({ success: false, message: "请填写豆包正式会话链接" }, { status: 400 })
      }
      recoveryBody = JSON.stringify({ expectedChatUrl: input.expectedChatUrl })
    }
    const response = await fetch(
      `${BACKEND_URL}${backendWorkflowRunsRoot(scope)}/${encodeURIComponent(runId)}/gaojixing/resume`,
      {
        method: "POST",
        headers: {
          ...forwardedRequestAuthHeaders(req),
          ...(recoveryBody ? { "Content-Type": "application/json" } : {}),
        },
        ...(recoveryBody ? { body: recoveryBody } : {}),
        cache: "no-store",
      },
    )
    const payload = await response.json().catch(() => null)
    return Response.json(payload, {
      status: response.status,
      headers: { "Cache-Control": "no-store" },
    })
  } catch (error) {
    return Response.json(
      {
        success: false,
        error: "GAOJIXING_RUN_RESUME_FAILED",
        message: error instanceof Error ? error.message : "Unknown Gaojixing Run resume error",
      },
      { status: 502 },
    )
  }
}

"use client"

import { useCallback, useEffect, useState } from "react"

import {
  fetchWorkflowOpenCLIAdapterNodes,
  workflowCatalogItemForOpenCLIAdapterNode,
  type WorkflowOpenCLIAdapterNodesResponse,
} from "./backend-opencli-adapter-nodes"
import {
  openCLIAdapterNodeToCatalogItem,
} from "./opencli-adapter-catalog"
import type { WorkflowNodeCatalogItem } from "./node-catalog"

type OpenCLIAdapterCatalogState = {
  items: WorkflowNodeCatalogItem[]
  response: WorkflowOpenCLIAdapterNodesResponse | null
  error: string | null
  loading: boolean
}

const INITIAL_STATE: OpenCLIAdapterCatalogState = {
  items: [],
  response: null,
  error: null,
  loading: false,
}

export function useOpenCLIAdapterCatalog(enabled = true) {
  const [state, setState] = useState<OpenCLIAdapterCatalogState>(INITIAL_STATE)

  const load = useCallback(async (signal?: AbortSignal, forceRefresh = false) => {
    setState((current) => ({ ...current, error: null, loading: true }))
    try {
      const response = await fetchWorkflowOpenCLIAdapterNodes({
        includeWrite: true,
        limit: 5000,
        refresh: forceRefresh,
        signal,
      })
      if (signal?.aborted) return
      setState({
        items: response.nodes.map((node) =>
          node.access === "read"
            ? workflowCatalogItemForOpenCLIAdapterNode(node)
            : openCLIAdapterNodeToCatalogItem(node),
        ),
        response,
        error: null,
        loading: false,
      })
    } catch (error) {
      if (signal?.aborted) return
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "OpenCLI adapter catalog unavailable",
        loading: false,
      }))
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      setState(INITIAL_STATE)
      return
    }
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [enabled, load])

  const refresh = useCallback(() => load(undefined, true), [load])

  return { ...state, refresh }
}

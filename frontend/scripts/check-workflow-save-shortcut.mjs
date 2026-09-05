import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { registerHooks, stripTypeScriptTypes } from 'node:module'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const reactStubUrl = 'workflow-keyboard-shortcuts-test:react'
const flowStoreStubUrl = 'workflow-keyboard-shortcuts-test:flow-store'
const settingsStoreStubUrl = 'workflow-keyboard-shortcuts-test:settings-store'

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'react') return { url: reactStubUrl, shortCircuit: true }
    if (specifier === '@/lib/flow/store') return { url: flowStoreStubUrl, shortCircuit: true }
    if (specifier === '@/lib/flow/settings-store') {
      return { url: settingsStoreStubUrl, shortCircuit: true }
    }
    if (specifier.startsWith('.') && context.parentURL?.startsWith('file:')) {
      const candidate = path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier)
      for (const resolvedPath of [candidate, `${candidate}.ts`, `${candidate}.tsx`]) {
        if (existsSync(resolvedPath)) {
          return { url: pathToFileURL(resolvedPath).href, shortCircuit: true }
        }
      }
    }
    return nextResolve(specifier, context)
  },
  load(url, context, nextLoad) {
    if (url === reactStubUrl) {
      return {
        format: 'module',
        source: `export function useEffect(effect) {
  globalThis.__workflowKeyboardShortcutEffect = effect
}`,
        shortCircuit: true,
      }
    }
    if (url === flowStoreStubUrl) {
      return {
        format: 'module',
        source: `export const useFlowStore = {
  getState() { return { nodes: [], networkStack: [], toolMode: 'select' } }
}`,
        shortCircuit: true,
      }
    }
    if (url === settingsStoreStubUrl) {
      return {
        format: 'module',
        source: `export const useSettingsStore = {
  getState() { return { showMiniMap: false } }
}`,
        shortCircuit: true,
      }
    }
    if (url.endsWith('.ts') || url.endsWith('.tsx')) {
      return {
        format: 'module',
        source: stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), {
          mode: 'strip',
          sourceUrl: url,
        }),
        shortCircuit: true,
      }
    }
    return nextLoad(url, context)
  },
})

const { useWorkflowKeyboardShortcuts } = await import(
  pathToFileURL(path.join(frontendRoot, 'components/flow/workflow-keyboard-shortcuts.ts')).href,
)

class FakeWindow {
  #listeners = new Map()

  addEventListener(type, listener) {
    if (!this.#listeners.has(type)) this.#listeners.set(type, new Set())
    this.#listeners.get(type).add(listener)
  }

  removeEventListener(type, listener) {
    this.#listeners.get(type)?.delete(listener)
  }

  dispatch(type, event) {
    for (const listener of this.#listeners.get(type) ?? []) listener(event)
  }

  listenerCount(type) {
    return this.#listeners.get(type)?.size ?? 0
  }
}

function keyEvent({ key, target, ctrlKey = false, metaKey = false, shiftKey = false, repeat = false }) {
  let defaultPrevented = false
  return {
    key,
    target,
    ctrlKey,
    metaKey,
    shiftKey,
    repeat,
    preventDefault() {
      defaultPrevented = true
    },
    get defaultPrevented() {
      return defaultPrevented
    },
  }
}

function shortcutOptions(overrides = {}) {
  const noop = () => {}
  return {
    autoLayout: async () => {},
    copy: noop,
    cut: noop,
    deleteSelected: noop,
    duplicate: noop,
    exitNodeNetwork: () => false,
    fitView: noop,
    groupSelection: noop,
    inspectorOpen: false,
    mousePosRef: { current: { x: 0, y: 0 } },
    paste: noop,
    projectSettingsOpen: false,
    redo: noop,
    save: noop,
    screenToFlowPosition: (position) => position,
    scissorCutRef: { current: new Set() },
    scissorDraggingRef: { current: false },
    setInspectorOpen: noop,
    setPaletteOpen: noop,
    setProjectSettingsOpen: noop,
    setScissorTrail: noop,
    setSettingsOpen: noop,
    setToolMode: noop,
    setMiniMapVisible: noop,
    settingsOpen: false,
    showToast: noop,
    undo: noop,
    yMomentaryModeRef: { current: null },
    ...overrides,
  }
}

function mountShortcuts(fakeWindow, overrides) {
  globalThis.window = fakeWindow
  delete globalThis.__workflowKeyboardShortcutEffect
  // This harness captures React's effect explicitly instead of mounting a renderer.
  // eslint-disable-next-line react-hooks/rules-of-hooks
  useWorkflowKeyboardShortcuts(shortcutOptions(overrides))
  const effect = globalThis.__workflowKeyboardShortcutEffect
  delete globalThis.__workflowKeyboardShortcutEffect
  assert.equal(typeof effect, 'function', 'the React useEffect stub should capture the effect')
  const cleanup = effect()
  assert.equal(typeof cleanup, 'function', 'the shortcut effect should return listener cleanup')
  return cleanup
}

const editableTargets = [
  ['INPUT', { tagName: 'INPUT' }],
  ['TEXTAREA', { tagName: 'TEXTAREA' }],
  ['contentEditable', { tagName: 'DIV', isContentEditable: true }],
  ['CANVAS', { tagName: 'CANVAS' }],
]

test('Ctrl+S and Meta+S save from every workflow focus target exactly once', () => {
  const previousWindow = globalThis.window
  const fakeWindow = new FakeWindow()
  let saveCalls = 0
  let announcements = 0
  const cleanup = mountShortcuts(fakeWindow, {
    save: () => { saveCalls += 1 },
    showToast: () => { announcements += 1 },
  })

  try {
    assert.equal(fakeWindow.listenerCount('keydown'), 1)
    assert.equal(fakeWindow.listenerCount('keyup'), 1)
    for (const [targetName, target] of editableTargets) {
      for (const [modifierName, modifier] of [['Ctrl', { ctrlKey: true }], ['Meta', { metaKey: true }]]) {
        const event = keyEvent({ key: 's', target, ...modifier })
        fakeWindow.dispatch('keydown', event)
        assert.equal(saveCalls, 1, `${modifierName}+S on ${targetName} should call save once`)
        assert.equal(event.defaultPrevented, true, `${modifierName}+S on ${targetName} should prevent browser save`)
        assert.equal(announcements, 0, `${modifierName}+S on ${targetName} should not announce a local result`)
        saveCalls = 0
      }
    }
  } finally {
    cleanup()
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})

test('typing and undo in editable fields remain untouched, and cleanup detaches listeners', () => {
  const previousWindow = globalThis.window
  const fakeWindow = new FakeWindow()
  let saveCalls = 0
  let undoCalls = 0
  let announcements = 0
  const cleanup = mountShortcuts(fakeWindow, {
    save: () => { saveCalls += 1 },
    showToast: () => { announcements += 1 },
    undo: () => { undoCalls += 1 },
  })

  try {
    for (const [, target] of editableTargets.slice(0, 3)) {
      for (const event of [
        keyEvent({ key: 'a', target }),
        keyEvent({ key: 'z', target, ctrlKey: true }),
        keyEvent({ key: 'z', target, metaKey: true }),
      ]) {
        fakeWindow.dispatch('keydown', event)
        assert.equal(event.defaultPrevented, false, 'typing and undo in an editable field should remain native')
      }
    }
    assert.equal(saveCalls, 0)
    assert.equal(undoCalls, 0)
    assert.equal(announcements, 0)

    cleanup()
    assert.equal(fakeWindow.listenerCount('keydown'), 0)
    assert.equal(fakeWindow.listenerCount('keyup'), 0)
    const afterCleanup = keyEvent({ key: 's', target: { tagName: 'CANVAS' }, ctrlKey: true })
    fakeWindow.dispatch('keydown', afterCleanup)
    assert.equal(afterCleanup.defaultPrevented, false, 'detached listeners should not prevent browser save')
    assert.equal(saveCalls, 0, 'detached listeners should not invoke save')
  } finally {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})

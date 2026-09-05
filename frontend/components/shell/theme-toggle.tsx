'use client'

import { Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])

  const isDark = resolvedTheme === 'dark'

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="切换主题"
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
    >
      <span className="grid size-4 place-items-center" aria-hidden>
        {mounted && isDark ? <Sun /> : <Moon />}
      </span>
    </Button>
  )
}

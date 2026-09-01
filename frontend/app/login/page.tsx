'use client'

import {
  ChevronDown,
  Droplets,
  Grid3X3,
  KeyRound,
  LoaderCircle,
  ShieldCheck,
  SquareTerminal,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { toast } from 'sonner'

import FaultyTerminal from '@/components/FaultyTerminal'
import Dither from '@/components/Dither'
import { useAuth } from '@/components/auth/auth-provider'
import { getSetupStatus } from '@/lib/api/endpoints'
import { PixelLiquidBg } from '@/components/unlumen-ui/pixel-liquid-bg'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import RevealText from '@/components/ui/smoothui/reveal-text'
import { sanitizeReturnTo } from '@/lib/auth/oidc'

type LoginBackdrop = 'liquid' | 'terminal' | 'pixel'

const BACKDROPS: Array<{ id: LoginBackdrop; label: string; icon: typeof Droplets }> = [
  { id: 'liquid', label: '流体', icon: Droplets },
  { id: 'terminal', label: '终端', icon: SquareTerminal },
  { id: 'pixel', label: '像素', icon: Grid3X3 },
]
const HEADLINE_WORDS = ['系统', '工作流', '数据产品']
const LIQUID_DARK_PALETTE = [
  '#050302',
  '#1a0805',
  '#351008',
  '#5b160c',
  '#8f2112',
  '#bd2d17',
  '#f0441f',
  '#ff6324',
  '#ff8a2b',
  '#ffad42',
  '#ffd36a',
  '#ffe596',
  '#fff0b0',
]
const LIQUID_LIGHT_PALETTE = [
  '#fff7d6',
  '#fff0b0',
  '#ffe596',
  '#ffd36a',
  '#ffad42',
  '#ff8a2b',
  '#ff6324',
  '#f0441f',
  '#bd2d17',
  '#8f2112',
]

function LoginBackground({ theme, reduceMotion }: { theme: LoginBackdrop; reduceMotion: boolean }) {
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={theme}
        className="absolute inset-0"
        initial={reduceMotion ? false : { opacity: 0, filter: 'blur(6px)', scale: 1.01 }}
        animate={{ opacity: 1, filter: 'blur(0px)', scale: 1 }}
        exit={reduceMotion ? { opacity: 0 } : { opacity: 0, filter: 'blur(3px)', scale: 0.995 }}
        transition={{ type: 'spring', duration: 0.45, bounce: 0 }}
      >
        {theme === 'liquid' ? (
          <div className="relative size-full overflow-hidden bg-[#070604]">
            <PixelLiquidBg
              className="absolute inset-0"
              darkPalette={LIQUID_DARK_PALETTE}
              lightPalette={LIQUID_LIGHT_PALETTE}
              pixelSize={11}
              resolution={0.34}
              mouseForce={5.5}
              cursorSize={150}
              viscosity={14}
              surfaceStrength={0.76}
              autoDemo={!reduceMotion}
            />
          </div>
        ) : theme === 'terminal' ? (
          <FaultyTerminal
            tint="#F97316"
            scale={1.35}
            gridMul={[2, 1]}
            digitSize={1.2}
            timeScale={0.22}
            pause={reduceMotion}
            scanlineIntensity={0.35}
            glitchAmount={0.65}
            flickerAmount={0.25}
            noiseAmp={0.85}
            chromaticAberration={0.5}
            curvature={0.08}
            mouseReact={!reduceMotion}
            mouseStrength={0.12}
            dpr={1}
            pageLoadAnimation={!reduceMotion}
            brightness={0.9}
          />
        ) : (
          <div className="relative size-full overflow-hidden bg-black">
            <div className="absolute inset-0 opacity-85">
              <Dither
                waveSpeed={0.1}
                waveFrequency={2.6}
                waveAmplitude={0.38}
                waveColor={[1, 0.27, 0.08]}
                colorNum={7}
                pixelSize={3}
                disableAnimation={reduceMotion}
                enableMouseInteraction={!reduceMotion}
                mouseRadius={0.72}
              />
            </div>
          </div>
        )}
      </motion.div>
    </AnimatePresence>
  )
}

function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const {
    status,
    oidcEnabled,
    developmentLoginEnabled,
    signInWithOidc,
    signInWithBootstrap,
    signInWithPassword,
    completeSetup,
    enterDevelopmentMode,
  } = useAuth()
  const [identityToken, setIdentityToken] = useState('')
  const [fleetToken, setFleetToken] = useState('')
  const [submitting, setSubmitting] = useState<
    'oidc' | 'bootstrap' | 'development' | 'password' | 'setup' | null
  >(null)
  const [reduceMotion, setReduceMotion] = useState(true)
  const [backdrop, setBackdrop] = useState<LoginBackdrop>('liquid')
  const [headlineWord, setHeadlineWord] = useState(0)
  const returnTo = sanitizeReturnTo(searchParams.get('returnTo'))

  // null = not yet resolved; defaults to the normal login form until the
  // (near-instant, local) check resolves, and fails soft to the login form
  // rather than blocking the page on a network hiccup.
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null)
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  useEffect(() => {
    let active = true
    getSetupStatus()
      .then((result) => {
        if (active && result) setSetupRequired(result.setup_required)
      })
      .catch(() => {
        if (active) setSetupRequired(false)
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    const syncPreference = () => setReduceMotion(mediaQuery.matches)
    syncPreference()
    mediaQuery.addEventListener('change', syncPreference)
    return () => mediaQuery.removeEventListener('change', syncPreference)
  }, [])

  useEffect(() => {
    if (status === 'authenticated' && submitting !== 'development') router.replace(returnTo)
  }, [returnTo, router, status, submitting])

  useEffect(() => {
    router.prefetch(returnTo)
  }, [returnTo, router])

  useEffect(() => {
    if (reduceMotion) return
    const interval = window.setInterval(
      () => setHeadlineWord((current) => (current + 1) % HEADLINE_WORDS.length),
      2800,
    )
    return () => window.clearInterval(interval)
  }, [reduceMotion])

  const optionalFleetToken = fleetToken.trim() || undefined

  async function startOidcLogin() {
    setSubmitting('oidc')
    try {
      await signInWithOidc(returnTo, optionalFleetToken)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '无法启动 OIDC 登录')
      setSubmitting(null)
    }
  }

  async function handleBootstrapLogin(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting('bootstrap')
    try {
      await signInWithBootstrap(identityToken, optionalFleetToken)
      toast.success('管理员身份验证成功')
      router.replace(returnTo)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '身份验证失败')
      setSubmitting(null)
    }
  }

  async function handleSetupSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (password !== confirmPassword) {
      toast.error('两次输入的密码不一致')
      return
    }
    setSubmitting('setup')
    try {
      await completeSetup(email, password, displayName.trim() || undefined, optionalFleetToken)
      toast.success('管理员账户已创建')
      router.replace(returnTo)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '创建管理员账户失败')
      setSubmitting(null)
    }
  }

  async function handlePasswordLogin(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting('password')
    try {
      await signInWithPassword(email, password, optionalFleetToken)
      toast.success('登录成功')
      router.replace(returnTo)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '登录失败')
      setSubmitting(null)
    }
  }

  function handleDevelopmentLogin() {
    setSubmitting('development')
    try {
      enterDevelopmentMode(optionalFleetToken)
      window.setTimeout(() => router.replace(returnTo), reduceMotion ? 0 : 285)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '无法进入本地开发模式')
      setSubmitting(null)
    }
  }

  return (
    <motion.main
      className="relative min-h-screen overflow-hidden bg-[#070604] text-white"
      animate={
        submitting === 'development' && !reduceMotion
          ? { x: '-100%' }
          : { x: 0 }
      }
      transition={{ duration: 0.28, ease: [0.32, 0.72, 0, 1] }}
    >
      <div className="absolute inset-0 opacity-90" aria-hidden="true">
        <LoginBackground theme={backdrop} reduceMotion={reduceMotion} />
      </div>
      <div
        className="absolute inset-0 bg-[linear-gradient(90deg,rgba(7,6,4,0.08),rgba(7,6,4,0.62)_52%,rgba(7,6,4,0.94)),linear-gradient(0deg,rgba(7,6,4,0.7),transparent_45%)]"
        aria-hidden="true"
      />

      <div className="absolute right-4 top-4 z-20 flex rounded-full border border-white/12 bg-black/45 p-1 backdrop-blur-xl sm:right-6 sm:top-6" aria-label="登录背景主题">
        {BACKDROPS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            aria-label={`${label}背景`}
            aria-pressed={backdrop === id}
            onClick={() => setBackdrop(id)}
            className="flex h-8 items-center gap-1.5 rounded-full px-2.5 text-xs text-white/55 transition-[color,background-color,transform] duration-200 [transition-timing-function:var(--motion-ease-settle)] hover:text-white active:scale-[0.94] aria-pressed:bg-white aria-pressed:text-black"
          >
            <Icon className="size-3.5" aria-hidden />
            <span className="hidden sm:inline">{label}</span>
          </button>
        ))}
      </div>

      <div className="relative mx-auto grid min-h-screen w-full max-w-7xl items-center gap-12 px-4 py-10 sm:px-8 lg:grid-cols-[minmax(0,1fr)_26rem] lg:px-12">
        <section className="hidden max-w-2xl lg:block" aria-label="产品介绍">
          <div className="mb-10 flex items-start gap-5 font-mono text-orange-200">
            <motion.span
              className="grid size-14 shrink-0 place-items-center rounded-lg border border-orange-300/45 bg-orange-500/15 text-base font-bold tracking-[-0.06em] text-orange-100 shadow-[0_0_30px_rgba(255,99,36,0.14)]"
              initial={reduceMotion ? false : { opacity: 0, scale: 0.9, y: 8, filter: 'blur(5px)' }}
              animate={{ opacity: 1, scale: 1, y: 0, filter: 'blur(0px)' }}
              transition={{ type: 'spring', duration: 0.5, bounce: 0.08 }}
            >
              OC
            </motion.span>
            <div className="min-w-0 pt-0.5">
              <div className="flex overflow-hidden text-[clamp(2.8rem,4.5vw,4.4rem)] font-bold leading-[0.86] tracking-[-0.075em] text-white/95">
                {Array.from('OPENCLI').map((character, index) => (
                  <RevealText
                    key={`${character}-${index}`}
                    delay={80 + index * 45}
                    direction="up"
                  >
                    {character}
                  </RevealText>
                ))}
                <span className="sr-only">OPENCLI</span>
              </div>
              <motion.div
                className="mt-3 flex items-center gap-3 pl-[clamp(1rem,4vw,4rem)] text-[11px] tracking-[0.34em] text-orange-200/75"
                initial={reduceMotion ? false : { opacity: 0, x: -18, filter: 'blur(4px)' }}
                animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
                transition={{ type: 'spring', duration: 0.55, bounce: 0, delay: 0.42 }}
              >
                <span className="h-px w-10 bg-orange-300/55" aria-hidden="true" />
                CONTROL / PLANE
                <span className="text-orange-200">_</span>
              </motion.div>
            </div>
          </div>
          <p className="mb-4 font-mono text-xs tracking-[0.22em] text-orange-300/80">
            COLLECT · ORCHESTRATE · OPERATE
          </p>
          <h1 className="max-w-lg text-balance text-[clamp(2.25rem,3.5vw,3.5rem)] font-medium leading-[1.08] tracking-[-0.045em]">
            <RevealText delay={180} direction="up" className="block">
              把分散的数据能力，
            </RevealText>
            <span className="block">
              编排成持续运行的
              <span className="relative ml-[0.08em] inline-grid overflow-hidden align-bottom">
                <span className="invisible col-start-1 row-start-1">数据产品</span>
                <AnimatePresence mode="wait" initial={false}>
                  <motion.span
                    key={HEADLINE_WORDS[headlineWord]}
                    className="col-start-1 row-start-1 bg-[length:300%_100%] bg-clip-text text-transparent"
                    style={{
                      backgroundImage:
                        'linear-gradient(90deg,#ff8a48 0%,#ffb15c 28%,#f5a6d8 52%,#c9a7ff 72%,#ffd36a 100%)',
                    }}
                    initial={reduceMotion ? false : { opacity: 0, y: '48%', backgroundPosition: '100% 50%' }}
                    animate={{ opacity: 1, y: 0, backgroundPosition: '35% 50%' }}
                    exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: '-45%' }}
                    transition={{ duration: 0.62, ease: [0.22, 1, 0.36, 1] }}
                  >
                    {HEADLINE_WORDS[headlineWord]}
                  </motion.span>
                </AnimatePresence>
              </span>
            </span>
          </h1>
          <p className="mt-6 max-w-lg text-pretty text-base leading-7 text-white/60">
            从采集节点、自动化执行到交付与消费，在同一个运营控制台里观察、修复和扩展。
          </p>
        </section>

        <div className="mx-auto flex w-full max-w-md flex-col gap-6 lg:mx-0">
          <div className="flex items-center gap-3 lg:hidden">
            <span className="grid size-14 place-items-center rounded-lg border border-orange-400/40 bg-orange-500/15 font-mono text-base font-black tracking-[-0.06em] text-orange-100">
              OC
            </span>
            <div>
              <h1 className="text-xl font-black tracking-[-0.055em]">OPENCLI</h1>
              <p className="text-sm text-white/55">采集编排与运营控制台</p>
            </div>
          </div>

          <Card className="border-white/12 bg-background/92 shadow-2xl shadow-black/40 backdrop-blur-xl">
            <CardHeader>
              <CardTitle>登录控制台</CardTitle>
              <CardDescription>
                使用组织账号登录，或使用本机管理员账户；Bootstrap Admin 仅用于紧急恢复。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              {oidcEnabled ? (
                <Button
                  className={`w-full overflow-hidden transition-[height,border-radius,background-color] duration-300 ${submitting === 'oidc' ? 'h-16 rounded-2xl' : 'h-10'}`}
                  data-triggered={submitting === 'oidc'}
                  disabled={submitting !== null}
                  onClick={startOidcLogin}
                >
                  {submitting === 'oidc' ? (
                    <span className="flex items-center gap-3 text-left">
                      <LoaderCircle className="size-5 animate-spin" />
                      <span className="grid">
                        <span>正在连接组织账号</span>
                        <span className="text-xs font-normal opacity-65">等待身份提供方响应</span>
                      </span>
                    </span>
                  ) : (
                    <><ShieldCheck />使用组织账号登录</>
                  )}
                </Button>
              ) : (
                <div className="rounded-lg border bg-muted/40 p-3 text-sm text-muted-foreground">
                  当前未配置组织登录。请配置 OIDC issuer、client ID 和授权端点。
                </div>
              )}

              {setupRequired ? (
                <form id="local-setup" onSubmit={handleSetupSubmit}>
                  <FieldGroup>
                    <Field>
                      <FieldLabel htmlFor="setup-email">管理员邮箱</FieldLabel>
                      <Input
                        id="setup-email"
                        type="email"
                        autoComplete="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        required
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="setup-display-name">显示名称（可选）</FieldLabel>
                      <Input
                        id="setup-display-name"
                        value={displayName}
                        onChange={(event) => setDisplayName(event.target.value)}
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="setup-password">设置密码</FieldLabel>
                      <Input
                        id="setup-password"
                        type="password"
                        autoComplete="new-password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        minLength={8}
                        required
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="setup-confirm-password">确认密码</FieldLabel>
                      <Input
                        id="setup-confirm-password"
                        type="password"
                        autoComplete="new-password"
                        value={confirmPassword}
                        onChange={(event) => setConfirmPassword(event.target.value)}
                        minLength={8}
                        required
                      />
                      <FieldDescription>首次部署：创建这个实例的第一个管理员账户。</FieldDescription>
                    </Field>
                  </FieldGroup>
                </form>
              ) : (
                <form id="local-login" onSubmit={handlePasswordLogin}>
                  <FieldGroup>
                    <Field>
                      <FieldLabel htmlFor="login-email">邮箱</FieldLabel>
                      <Input
                        id="login-email"
                        type="email"
                        autoComplete="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        required
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="login-password">密码</FieldLabel>
                      <Input
                        id="login-password"
                        type="password"
                        autoComplete="current-password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        required
                      />
                    </Field>
                  </FieldGroup>
                </form>
              )}

              <details className="group rounded-lg border border-white/12 bg-black/20">
                <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-medium text-white/70">
                  紧急管理员访问
                  <ChevronDown className="size-4 text-white/40 transition-transform group-open:rotate-180" />
                </summary>
                <form
                  id="bootstrap-login"
                  onSubmit={handleBootstrapLogin}
                  className="border-t border-white/12 p-4"
                >
                  <FieldGroup>
                    <Field>
                      <FieldLabel htmlFor="identity-token">管理员身份令牌</FieldLabel>
                      <Input
                        id="identity-token"
                        type="password"
                        placeholder="BOOTSTRAP_ADMIN_TOKEN"
                        value={identityToken}
                        onChange={(event) => setIdentityToken(event.target.value)}
                        autoComplete="off"
                      />
                      <FieldDescription>验证成功后仅保存在当前标签页会话中。</FieldDescription>
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="fleet-token">Fleet API 令牌（可选）</FieldLabel>
                      <Input
                        id="fleet-token"
                        type="password"
                        placeholder="API_AUTH_TOKEN"
                        value={fleetToken}
                        onChange={(event) => setFleetToken(event.target.value)}
                        autoComplete="off"
                      />
                      <FieldDescription>
                        后端启用 Fleet Auth 时填写；留空沿用部署配置或浏览器中已有值。
                      </FieldDescription>
                    </Field>
                    <Button
                      type="submit"
                      variant="outline"
                      size="sm"
                      className={`w-full overflow-hidden transition-[height,border-radius,background-color] duration-300 ${submitting === 'bootstrap' ? 'h-16 rounded-2xl' : ''}`}
                      data-triggered={submitting === 'bootstrap'}
                      disabled={submitting !== null}
                    >
                      {submitting === 'bootstrap' ? (
                        <span className="flex items-center gap-3 text-left">
                          <LoaderCircle className="size-5 animate-spin" />
                          <span className="grid">
                            <span>正在验证管理员令牌</span>
                            <span className="text-xs font-normal opacity-65">验证通过后建立本地会话</span>
                          </span>
                        </span>
                      ) : (
                        <><KeyRound />使用管理员令牌登录</>
                      )}
                    </Button>
                  </FieldGroup>
                </form>
              </details>
            </CardContent>
            <CardFooter className="flex-col gap-2">
              {setupRequired ? (
                <Button
                  type="submit"
                  form="local-setup"
                  className={`w-full overflow-hidden transition-[height,border-radius,background-color] duration-300 ${submitting === 'setup' ? 'h-16 rounded-2xl' : 'h-10'}`}
                  data-triggered={submitting === 'setup'}
                  disabled={submitting !== null}
                >
                  {submitting === 'setup' ? (
                    <span className="flex items-center gap-3 text-left">
                      <LoaderCircle className="size-5 animate-spin" />
                      <span className="grid">
                        <span>正在创建管理员账户</span>
                        <span className="text-xs font-normal opacity-65">即将自动登录</span>
                      </span>
                    </span>
                  ) : (
                    '创建管理员账户'
                  )}
                </Button>
              ) : (
                <Button
                  type="submit"
                  form="local-login"
                  variant={oidcEnabled ? 'outline' : 'default'}
                  className={`w-full overflow-hidden transition-[height,border-radius,background-color] duration-300 ${submitting === 'password' ? 'h-16 rounded-2xl' : 'h-10'}`}
                  data-triggered={submitting === 'password'}
                  disabled={submitting !== null}
                >
                  {submitting === 'password' ? (
                    <span className="flex items-center gap-3 text-left">
                      <LoaderCircle className="size-5 animate-spin" />
                      <span>正在登录</span>
                    </span>
                  ) : (
                    '登录'
                  )}
                </Button>
              )}
              {developmentLoginEnabled ? (
                <Button
                  type="button"
                  variant="ghost"
                  className={`w-full overflow-hidden text-muted-foreground transition-[height,border-radius,background-color] duration-300 ${submitting === 'development' ? 'h-16 rounded-2xl bg-muted' : 'h-10'}`}
                  data-triggered={submitting === 'development'}
                  disabled={submitting !== null}
                  onClick={handleDevelopmentLogin}
                >
                  {submitting === 'development' ? (
                    <span className="flex items-center gap-3 text-left text-foreground">
                      <LoaderCircle className="size-5 animate-spin" />
                      <span className="grid">
                        <span>正在进入本地开发模式</span>
                        <span className="text-xs font-normal text-muted-foreground">正在建立开发会话</span>
                      </span>
                    </span>
                  ) : (
                    '进入本地开发模式'
                  )}
                </Button>
              ) : null}
            </CardFooter>
          </Card>
          <p className="text-center font-mono text-[11px] tracking-wide text-white/35">
            LOCAL-FIRST · AUDITABLE · NODE-NATIVE
          </p>
        </div>
      </div>
    </motion.main>
  )
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  )
}

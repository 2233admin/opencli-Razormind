import { redirect } from 'next/navigation'

type ControlActionsRedirectProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}

export default async function ControlActionsPage({ searchParams }: ControlActionsRedirectProps) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(await searchParams)) {
    if (Array.isArray(value)) value.forEach((item) => query.append(key, item))
    else if (value !== undefined) query.set(key, value)
  }
  query.set('tab', 'controls')
  redirect(`/inbox?${query.toString()}`)
}

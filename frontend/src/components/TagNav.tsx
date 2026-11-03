import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Badge, Button, Group, Text, UnstyledButton } from '@mantine/core'
import { IconChevronDown, IconChevronRight, IconHash } from '@tabler/icons-react'
import { useState } from 'react'
import { useTags } from '../lib/queries'

// Up to this many tags are listed directly; more are grouped by first letter.
export const LETTER_INDEX_MIN = 16
const COLLAPSED_KEY = 'reelvault.tag-nav.collapsed'

function storedCollapsed(): boolean {
  try { return localStorage.getItem(COLLAPSED_KEY) === '1' } catch { return false }
}

function byLetter(a: string, b: string): number {
  return a === '#' ? 1 : b === '#' ? -1 : a.localeCompare(b)
}

export default function TagNav({ active, onSelect }: { active: string | null; onSelect: (tag: string | null) => void }) {
  useTranslation()

  const tags = useTags()
  const [collapsed, setCollapsed] = useState(storedCollapsed)
  // undefined follows the active tag's letter; null keeps every letter closed.
  const [letter, setLetter] = useState<string | null>()
  const used = (tags.data ?? []).filter((t) => t.count > 0)
  if (!used.length) return null

  const toggle = () => {
    const next = !collapsed
    setCollapsed(next)
    try { localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0') } catch { /* per-device preference only */ }
  }
  const grouped = used.length > LETTER_INDEX_MIN
  const letters = [...new Set(used.map((t) => t.initial))].sort(byLetter)
  const open = letter === undefined ? (used.find((t) => t.name === active)?.initial ?? null) : letter
  const shown = grouped ? used.filter((t) => t.initial === open) : used

  return (
    <>
      <UnstyledButton onClick={toggle} aria-expanded={!collapsed} mt="sm" px="sm" w="100%">
        <Group gap={4}>
          {collapsed ? <IconChevronRight size={12} /> : <IconChevronDown size={12} />}
          <Text size="xs" c="dimmed" fw={600}>{tr("标签")}</Text>
          <Text size="xs" c="dimmed">{used.length}</Text>
        </Group>
      </UnstyledButton>
      {!collapsed && grouped && (
        <Group gap={2} px="sm" pt={6}>
          {letters.map((l) => (
            <Button key={l} size="compact-xs" px={6} miw={24}
              variant={open === l ? 'filled' : 'subtle'} color="violet"
              aria-pressed={open === l}
              aria-label={l === '#' ? tr("其他标签") : tr("{{v0}} 开头的标签", { v0: l })}
              onClick={() => setLetter(open === l ? null : l)}>
              {l}
            </Button>
          ))}
        </Group>
      )}
      {!collapsed && shown.length > 0 && (
        <Group gap={6} p="sm">
          {shown.map((t) => (
            <Badge
              component="button" type="button"
              key={t.name}
              aria-pressed={active === t.name}
              color={t.color ?? 'violet'} autoContrast
              variant={active === t.name ? 'filled' : 'light'}
              leftSection={<IconHash size={10} />}
              style={{ cursor: 'pointer', textTransform: 'none' }}
              onClick={() => onSelect(active === t.name ? null : t.name)}
            >
              {t.name} {t.count}
            </Badge>
          ))}
        </Group>
      )}
    </>
  )
}

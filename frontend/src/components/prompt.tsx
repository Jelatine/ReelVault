import { tr } from '../lib/i18n'
import { modals } from '@mantine/modals'
import PromptBody from './PromptBody'

/** Ask for a line of text in a modal; resolves to null when cancelled. */
export function promptText(title: string, label: string, initial = ''): Promise<string | null> {
  return new Promise((resolve) => {
    let done = false
    const id = modals.open({
      title,
      onClose: () => {
        if (!done) resolve(null)
      },
      children: (
        <PromptBody
          initial={initial}
          label={label}
          onSubmit={(v) => {
            done = true
            modals.close(id)
            resolve(v)
          }}
        />
      ),
    })
  })
}

export function confirmAction(opts: {
  title: string
  message: React.ReactNode
  confirm?: string
  danger?: boolean
}): Promise<boolean> {
  return new Promise((resolve) => {
    modals.openConfirmModal({
      title: opts.title,
      children: opts.message,
      labels: { confirm: opts.confirm ?? tr("确定"), cancel: tr("取消") },
      confirmProps: opts.danger ? { color: 'red' } : undefined,
      onConfirm: () => resolve(true),
      onCancel: () => resolve(false),
      onClose: () => resolve(false),
    })
  })
}

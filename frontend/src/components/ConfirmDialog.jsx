import { useEffect, useRef } from 'react'

import { Button } from './ui'

/**
 * Asks before something that is hard to undo: rejecting a request, turning
 * someone's access off, removing an admin. The confirm button names the
 * action ("Disable access"), never just "Yes".
 */
export function ConfirmDialog({ open, title, children, confirm, tone = 'danger',
                                busy, onConfirm, onCancel }) {
  const ref = useRef(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal?.()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby="dialog-title"
      onCancel={(e) => {
        e.preventDefault()
        onCancel()
      }}
    >
      <h2 id="dialog-title" className="dialog__title">{title}</h2>
      <div className="dialog__body">{children}</div>
      <div className="dialog__actions">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button type="button" variant={tone} onClick={onConfirm} disabled={busy}>
          {busy ? 'Working…' : confirm}
        </Button>
      </div>
    </dialog>
  )
}

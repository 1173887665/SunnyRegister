import { cloneElement, isValidElement, useEffect, useRef, useState } from 'react'
import type { ReactElement, ReactNode } from 'react'

type ConfirmBubbleProps = {
  message: ReactNode
  detail?: ReactNode
  onConfirm: () => void | Promise<void>
  children: ReactElement
  confirmLabel?: string
  cancelLabel?: string
  disabled?: boolean
  align?: 'left' | 'right'
}

export function ConfirmBubble({
  message,
  detail,
  onConfirm,
  children,
  confirmLabel = '确定',
  cancelLabel = '取消',
  disabled = false,
  align = 'right',
}: ConfirmBubbleProps) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const ref = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  const trigger = isValidElement(children)
    ? cloneElement(children as ReactElement<any>, {
        onClick: (event: React.MouseEvent) => {
          event.preventDefault()
          event.stopPropagation()
          if (disabled || (children.props as any).disabled) return
          setOpen((value) => !value)
        },
      })
    : children

  const confirm = async () => {
    setLoading(true)
    try {
      await onConfirm()
      setOpen(false)
    } finally {
      setLoading(false)
    }
  }

  return (
    <span ref={ref} className="confirm-bubble-wrap">
      {trigger}
      {open && (
        <span className={`confirm-bubble confirm-bubble-${align}`}>
          <span className="confirm-bubble-title">{message}</span>
          {detail ? <span className="confirm-bubble-detail">{detail}</span> : null}
          <span className="confirm-bubble-actions">
            <button type="button" className="confirm-bubble-cancel" onClick={() => setOpen(false)} disabled={loading}>
              {cancelLabel}
            </button>
            <button type="button" className="confirm-bubble-ok" onClick={confirm} disabled={loading}>
              {loading ? '...' : confirmLabel}
            </button>
          </span>
        </span>
      )}
    </span>
  )
}

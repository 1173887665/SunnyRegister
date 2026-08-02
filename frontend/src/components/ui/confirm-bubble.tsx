import { cloneElement, isValidElement, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties, ReactElement, ReactNode } from 'react'
import { createPortal } from 'react-dom'

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
  const [position, setPosition] = useState<{ top: number; left: number; anchorX: number; placement: 'top' | 'bottom' } | null>(null)
  const ref = useRef<HTMLSpanElement | null>(null)
  const bubbleRef = useRef<HTMLSpanElement | null>(null)

  const updatePosition = useCallback(() => {
    const trigger = ref.current
    const bubble = bubbleRef.current
    if (!trigger || !bubble) return
    const triggerRect = trigger.getBoundingClientRect()
    const bubbleRect = bubble.getBoundingClientRect()
    const gap = 10
    const edge = 10
    const fitsBelow = triggerRect.bottom + gap + bubbleRect.height <= window.innerHeight - edge
    const fitsAbove = triggerRect.top - gap - bubbleRect.height >= edge
    const placement = !fitsBelow && fitsAbove ? 'top' : 'bottom'
    const desiredLeft = align === 'right' ? triggerRect.right - bubbleRect.width : triggerRect.left
    const maxLeft = Math.max(edge, window.innerWidth - bubbleRect.width - edge)
    const left = Math.min(Math.max(edge, desiredLeft), maxLeft)
    const top = placement === 'top'
      ? Math.max(edge, triggerRect.top - bubbleRect.height - gap)
      : Math.min(window.innerHeight - bubbleRect.height - edge, triggerRect.bottom + gap)
    const anchorX = Math.min(Math.max(18, triggerRect.left + triggerRect.width / 2 - left), bubbleRect.width - 18)
    setPosition({ top, left, anchorX, placement })
  }, [align])

  useLayoutEffect(() => {
    if (open) updatePosition()
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      const target = event.target as Node
      if (!ref.current?.contains(target) && !bubbleRef.current?.contains(target)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    const reposition = () => updatePosition()
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', closeOnEscape)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', closeOnEscape)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open, updatePosition])

  const trigger = isValidElement(children)
    ? cloneElement(children as ReactElement<any>, {
        onClick: (event: React.MouseEvent) => {
          event.preventDefault()
          event.stopPropagation()
          if (disabled || (children.props as any).disabled) return
          setPosition(null)
          setOpen((value) => !value)
        },
        'aria-expanded': open,
        'aria-haspopup': 'dialog',
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

  const bubble = open && typeof document !== 'undefined'
    ? createPortal(
        <span
          ref={bubbleRef}
          role="alertdialog"
          aria-modal="false"
          className={`confirm-bubble confirm-bubble-${align} confirm-bubble-${position?.placement || 'bottom'}`}
          style={{
            top: position?.top ?? 0,
            left: position?.left ?? 0,
            visibility: position ? 'visible' : 'hidden',
            '--confirm-anchor-x': `${position?.anchorX ?? 24}px`,
          } as CSSProperties}
        >
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
        </span>,
        document.body,
      )
    : null

  return (
    <span ref={ref} className="confirm-bubble-wrap">
      {trigger}
      {bubble}
    </span>
  )
}

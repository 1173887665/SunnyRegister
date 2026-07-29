import type { ReactElement, ReactNode } from 'react'
import { Popconfirm } from 'antd'

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
  return (
    <Popconfirm
      title={message}
      description={detail}
      onConfirm={onConfirm}
      okText={confirmLabel}
      cancelText={cancelLabel}
      disabled={disabled}
      placement={align === 'left' ? 'bottomLeft' : 'bottomRight'}
      overlayClassName="sunny-ant-popconfirm"
    >
      {children}
    </Popconfirm>
  )
}

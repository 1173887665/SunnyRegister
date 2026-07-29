import * as React from 'react'
import { Button as AntButton } from 'antd'
import type { ButtonProps as AntButtonProps } from 'antd'
import { cn } from '@/lib/utils'

type SunnyButtonVariant = 'default' | 'destructive' | 'outline' | 'ghost' | 'link'
type SunnyButtonSize = 'default' | 'sm' | 'lg' | 'icon'

export interface ButtonProps extends Omit<AntButtonProps, 'type' | 'size' | 'variant'> {
  variant?: SunnyButtonVariant
  size?: SunnyButtonSize
}

const Button = React.forwardRef<React.ComponentRef<typeof AntButton>, ButtonProps>(
  ({ className, variant = 'default', size = 'default', danger, children, ...props }, ref) => {
    const type = variant === 'outline' ? 'default' : variant === 'ghost' ? 'text' : variant === 'link' ? 'link' : 'primary'
    const antSize = size === 'sm' ? 'small' : size === 'lg' ? 'large' : 'middle'
    return (
      <AntButton
        ref={ref}
        type={type}
        size={antSize}
        danger={danger || variant === 'destructive'}
        className={cn('sunny-ant-button', size === 'icon' && 'sunny-ant-button-icon', className)}
        {...props}
      >
        {children}
      </AntButton>
    )
  },
)
Button.displayName = 'Button'

export { Button }

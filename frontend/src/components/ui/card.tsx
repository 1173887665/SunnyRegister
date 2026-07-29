import * as React from 'react'
import { Card as AntCard } from 'antd'
import { cn } from '@/lib/utils'

const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...props }, ref) => (
    <AntCard
      ref={ref}
      className={cn('sunny-ant-card border border-[var(--border)] bg-[var(--bg-card)]', className)}
      styles={{ body: { padding: 0 } }}
      {...props}
    >
      {children}
    </AntCard>
  ),
)
Card.displayName = 'Card'

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('mb-3 flex flex-col space-y-1', className)} {...props} />
  ),
)
CardHeader.displayName = 'CardHeader'

const CardTitle = React.forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h3 ref={ref} className={cn('text-base font-semibold text-[var(--text-primary)]', className)} {...props} />
  ),
)
CardTitle.displayName = 'CardTitle'

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => <div ref={ref} className={className} {...props} />,
)
CardContent.displayName = 'CardContent'

export { Card, CardHeader, CardTitle, CardContent }

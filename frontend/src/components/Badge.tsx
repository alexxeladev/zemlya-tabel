type Variant = 'gray' | 'green' | 'red' | 'blue' | 'amber'

const CLASSES: Record<Variant, string> = {
  gray: 'bg-gray-100 text-gray-700',
  green: 'bg-green-100 text-green-700',
  red: 'bg-red-100 text-red-700',
  blue: 'bg-blue-100 text-blue-700',
  amber: 'bg-amber-100 text-amber-700',
}

interface Props {
  variant?: Variant
  children: React.ReactNode
}

export function Badge({ variant = 'gray', children }: Props) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${CLASSES[variant]}`}>
      {children}
    </span>
  )
}

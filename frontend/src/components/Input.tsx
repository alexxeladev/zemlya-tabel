import { forwardRef, type InputHTMLAttributes } from 'react'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  error?: boolean
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ error = false, className = '', ...props }, ref) => (
    <input
      ref={ref}
      className={`w-full rounded-md border px-3 py-2 text-sm shadow-sm transition-colors placeholder:text-gray-400 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50 ${
        error
          ? 'border-red-400 bg-red-50 focus:ring-red-300'
          : 'border-gray-300 bg-white focus:border-brand focus:ring-blue-200'
      } ${className}`}
      {...props}
    />
  ),
)
Input.displayName = 'Input'

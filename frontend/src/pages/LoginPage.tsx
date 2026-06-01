import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate, useLocation } from 'react-router-dom'
import { z } from 'zod'
import { useAuthStore } from '../store/auth'
import { ApiError } from '../api/client'
import { Button } from '../components/Button'
import { Input } from '../components/Input'
import { FormField } from '../components/FormField'
import { ErrorBox } from '../components/ErrorBox'

const schema = z.object({
  email: z.string().email({ message: 'Введите корректный email' }),
  password: z.string().min(6, { message: 'Минимум 6 символов' }),
})

type FormData = z.infer<typeof schema>

export function LoginPage() {
  const [serverError, setServerError] = useState<string | null>(null)
  const login = useAuthStore((s) => s.login)
  const navigate = useNavigate()
  const location = useLocation()

  const from =
    (location.state as { from?: { pathname: string } } | null)?.from?.pathname ?? '/dashboard'

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormData>({ resolver: zodResolver(schema) })

  const onSubmit = async (data: FormData) => {
    setServerError(null)
    try {
      await login(data.email, data.password)
      const { mustChangePassword } = useAuthStore.getState()
      navigate(mustChangePassword ? '/change-password' : from, { replace: true })
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setServerError('Неверный email или пароль')
        } else if (err.status === 403) {
          setServerError('Учётная запись заблокирована')
        } else {
          setServerError(err.message || 'Ошибка сервера')
        }
      } else {
        setServerError('Ошибка сервера')
      }
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <div className="space-y-1 text-center">
          <h1 className="text-3xl font-bold text-brand">Табель</h1>
          <p className="text-sm text-gray-500">Вход в систему</p>
        </div>
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4">
          <FormField label="Email" error={errors.email?.message}>
            <Input
              {...register('email')}
              type="email"
              placeholder="admin@example.com"
              autoComplete="email"
              error={!!errors.email}
            />
          </FormField>
          <FormField label="Пароль" error={errors.password?.message}>
            <Input
              {...register('password')}
              type="password"
              placeholder="••••••••"
              autoComplete="current-password"
              error={!!errors.password}
            />
          </FormField>
          <Button type="submit" loading={isSubmitting} className="w-full">
            Войти
          </Button>
          <ErrorBox message={serverError} />
        </form>
      </div>
    </div>
  )
}

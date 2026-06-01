import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { changePassword } from '../api/auth'
import { useAuthStore } from '../store/auth'
import { Button } from '../components/Button'
import { Input } from '../components/Input'
import { FormField } from '../components/FormField'
import { ErrorBox } from '../components/ErrorBox'

const schema = z
  .object({
    currentPassword: z.string().min(1, { message: 'Введите текущий пароль' }),
    newPassword: z.string().min(8, { message: 'Минимум 8 символов' }),
    confirmPassword: z.string().min(1, { message: 'Подтвердите пароль' }),
  })
  .refine((d) => d.newPassword !== d.currentPassword, {
    message: 'Новый пароль должен отличаться от текущего',
    path: ['newPassword'],
  })
  .refine((d) => d.confirmPassword === d.newPassword, {
    message: 'Пароли не совпадают',
    path: ['confirmPassword'],
  })

type FormData = z.infer<typeof schema>

export function ChangePasswordPage() {
  const [serverError, setServerError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const refreshUser = useAuthStore((s) => s.refreshUser)
  const navigate = useNavigate()

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormData>({ resolver: zodResolver(schema) })

  const onSubmit = async (data: FormData) => {
    setServerError(null)
    try {
      await changePassword(data.currentPassword, data.newPassword)
      await refreshUser()
      setSuccess(true)
      setTimeout(() => navigate('/dashboard', { replace: true }), 1500)
    } catch (err) {
      setServerError((err as Error).message || 'Ошибка сервера')
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold text-gray-900">Сменить пароль</h1>
          <p className="text-sm text-gray-500">При первом входе необходимо сменить пароль</p>
        </div>

        {success ? (
          <div className="flex items-center gap-2 rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-700">
            <svg className="h-4 w-4 shrink-0" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                clipRule="evenodd"
              />
            </svg>
            <span>Пароль изменён. Переход на главную…</span>
          </div>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4">
            <FormField label="Текущий пароль" error={errors.currentPassword?.message}>
              <Input
                {...register('currentPassword')}
                type="password"
                placeholder="••••••••"
                autoComplete="current-password"
                error={!!errors.currentPassword}
              />
            </FormField>
            <FormField label="Новый пароль" error={errors.newPassword?.message}>
              <Input
                {...register('newPassword')}
                type="password"
                placeholder="••••••••"
                autoComplete="new-password"
                error={!!errors.newPassword}
              />
            </FormField>
            <FormField label="Повтор нового пароля" error={errors.confirmPassword?.message}>
              <Input
                {...register('confirmPassword')}
                type="password"
                placeholder="••••••••"
                autoComplete="new-password"
                error={!!errors.confirmPassword}
              />
            </FormField>
            <Button type="submit" loading={isSubmitting} className="w-full">
              Сменить пароль
            </Button>
            <ErrorBox message={serverError} />
          </form>
        )}
      </div>
    </div>
  )
}

import axios from 'axios'

export const TOKEN_KEY = 'auth_token'

export class ApiError extends Error {
  readonly status?: number
  /** Сырой `detail` ответа. Нужен там, где бэк отвечает не строкой, а разбором:
   *  например 409 на смену дат периода работы несёт числа «сколько дней и на
   *  какую сумму очистится» (task_employment_period). Без него эти числа
   *  доходили бы до экрана только склеенным JSON-ом. */
  readonly detail?: unknown
  constructor(message: string, status?: number, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL as string,
})

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    const status = axios.isAxiosError(error) ? error.response?.status : undefined

    if (status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      if (window.location.pathname !== '/login') {
        window.location.replace('/login')
      }
    }

    const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined
    // 422 от pydantic — список ошибок полей. Показываем их тексты (например,
    // причину отказа политики паролей), а не склеенный JSON.
    const validationMessages = Array.isArray(detail)
      ? detail
          .map((d) => (d && typeof d === 'object' && 'msg' in d ? String(d.msg) : ''))
          .filter(Boolean)
          .map((m) => m.replace(/^Value error, /, ''))
      : []
    const message =
      typeof detail === 'string'
        ? detail
        : validationMessages.length > 0
          ? validationMessages.join('; ')
          : detail != null
          ? JSON.stringify(detail)
          : axios.isAxiosError(error)
            ? error.message
            : 'Ошибка сервера'

    return Promise.reject(new ApiError(message, status, detail))
  },
)

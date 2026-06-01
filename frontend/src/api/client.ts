import axios from 'axios'

export const TOKEN_KEY = 'auth_token'

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
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
    const message =
      typeof detail === 'string'
        ? detail
        : detail != null
          ? JSON.stringify(detail)
          : axios.isAxiosError(error)
            ? error.message
            : 'Ошибка сервера'

    return Promise.reject(new ApiError(message, status))
  },
)

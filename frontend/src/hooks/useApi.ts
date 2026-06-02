import { useCallback, useEffect, useRef, useState } from 'react'

interface ApiState<T> {
  data: T | null
  isLoading: boolean
  error: string | null
}

export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<ApiState<T>>({ data: null, isLoading: true, error: null })
  const fnRef = useRef(fn)
  fnRef.current = fn

  const fetch = useCallback(async () => {
    setState((s) => ({ ...s, isLoading: true, error: null }))
    try {
      const data = await fnRef.current()
      setState({ data, isLoading: false, error: null })
    } catch (err) {
      setState({ data: null, isLoading: false, error: err instanceof Error ? err.message : 'Ошибка' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    fetch()
  }, [fetch])

  return { ...state, refetch: fetch }
}

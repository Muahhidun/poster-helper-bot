import { useState, useEffect } from 'react'

interface UseApiOptions {
  enabled?: boolean
  onSuccess?: (data: any) => void
  onError?: (error: Error) => void
}

export function useApi<T>(
  fetcher: () => Promise<T>,
  options: UseApiOptions = {}
) {
  const { enabled = true, onSuccess, onError } = options
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState<boolean>(enabled)
  const [error, setError] = useState<Error | null>(null)

  const refetch = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetcher()
      setData(result)
      onSuccess?.(result)
    } catch (err) {
      const error = err instanceof Error ? err : new Error('Unknown error')
      setError(error)
      onError?.(error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (enabled) {
      refetch()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled])

  return { data, loading, error, refetch }
}

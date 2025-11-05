import React, { useState } from 'react'
import { useTelegram } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { SupplyCard } from '../components/SupplyCard'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'

export const SupplyHistory: React.FC = () => {
  const { themeParams } = useTelegram()
  const [page, setPage] = useState(1)

  const { data: response, loading, error, refetch } = useApi(
    () => getApiClient().getSupplies(page, 20)
  )

  if (loading && !response) return <Loading />
  if (error && !response) return <ErrorMessage message={error.message} onRetry={refetch} />
  if (!response) return null

  const groupByDate = (supplies: typeof response.supplies) => {
    const groups: Record<string, typeof response.supplies> = {}

    supplies.forEach((supply) => {
      const date = new Date(supply.created_at)
      const dateKey = date.toLocaleDateString('ru-RU', {
        day: 'numeric',
        month: 'long',
        year: 'numeric',
      })

      if (!groups[dateKey]) {
        groups[dateKey] = []
      }
      groups[dateKey].push(supply)
    })

    return groups
  }

  const groupedSupplies = groupByDate(response.supplies)

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen">
      <Header title="История поставок" showBack />

      <div className="p-4">
        {Object.entries(groupedSupplies).map(([date, supplies]) => (
          <div key={date} className="mb-6">
            <h3
              className="text-lg font-semibold mb-3"
              style={{ color: themeParams.text_color || '#000000' }}
            >
              📅 {date}
            </h3>
            {supplies.map((supply) => (
              <SupplyCard key={supply.id} supply={supply} />
            ))}
          </div>
        ))}

        {response.pages > 1 && (
          <div className="flex justify-center gap-2 mt-6">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-4 py-2 rounded-lg disabled:opacity-50"
              style={{
                backgroundColor: themeParams.button_color || '#3b82f6',
                color: themeParams.button_text_color || '#ffffff',
              }}
            >
              ← Назад
            </button>
            <span
              className="px-4 py-2"
              style={{ color: themeParams.text_color || '#000000' }}
            >
              {page} / {response.pages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(response.pages, p + 1))}
              disabled={page === response.pages}
              className="px-4 py-2 rounded-lg disabled:opacity-50"
              style={{
                backgroundColor: themeParams.button_color || '#3b82f6',
                color: themeParams.button_text_color || '#ffffff',
              }}
            >
              Вперёд →
            </button>
          </div>
        )}

        {response.supplies.length === 0 && (
          <div className="text-center py-12">
            <div className="text-6xl mb-4">📦</div>
            <div
              className="text-lg"
              style={{ color: themeParams.hint_color || '#6b7280' }}
            >
              Пока нет поставок
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

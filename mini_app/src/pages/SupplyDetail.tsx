import React from 'react'
import { useParams } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'

export const SupplyDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const { themeParams } = useTelegram()

  const { data: supply, loading, error, refetch } = useApi(
    () => getApiClient().getSupply(Number(id))
  )

  if (loading) return <Loading />
  if (error) return <ErrorMessage message={error.message} onRetry={refetch} />
  if (!supply) return null

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleString('ru-RU', {
      day: 'numeric',
      month: 'long',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 90) return '#10b981' // green
    if (confidence >= 75) return '#f59e0b' // yellow
    return '#ef4444' // red
  }

  const avgConfidence = supply.items.reduce((sum, item) => sum + item.confidence_score, 0) / supply.items.length

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen">
      <Header title={`Поставка #${supply.id}`} showBack />

      <div className="p-4">
        <div
          className="p-4 rounded-lg mb-6"
          style={{
            backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
          }}
        >
          <div className="mb-2">
            <span className="text-2xl mr-2">🏪</span>
            <span
              className="text-lg font-semibold"
              style={{ color: themeParams.text_color || '#000000' }}
            >
              {supply.supplier_name}
            </span>
          </div>

          {supply.account_name && (
            <div
              className="text-sm mb-1"
              style={{ color: themeParams.hint_color || '#6b7280' }}
            >
              💰 Счёт: {supply.account_name}
            </div>
          )}

          {supply.storage_name && (
            <div
              className="text-sm mb-1"
              style={{ color: themeParams.hint_color || '#6b7280' }}
            >
              📦 Склад: {supply.storage_name}
            </div>
          )}

          <div
            className="text-sm"
            style={{ color: themeParams.hint_color || '#6b7280' }}
          >
            📅 {formatDate(supply.created_at)}
          </div>
        </div>

        <div className="mb-6">
          <h3
            className="text-lg font-semibold mb-3"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            📋 Товары ({supply.items.length} позиций)
          </h3>

          {supply.items.map((item, idx) => (
            <div
              key={idx}
              className="p-4 rounded-lg mb-3"
              style={{
                backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
              }}
            >
              <div className="flex justify-between items-start mb-2">
                <div className="flex-1">
                  <div
                    className="font-medium"
                    style={{ color: themeParams.text_color || '#000000' }}
                  >
                    {idx + 1}. {item.matched_item_name}
                  </div>
                  <div
                    className="text-sm"
                    style={{ color: themeParams.hint_color || '#6b7280' }}
                  >
                    Распознано: "{item.original_text}"
                  </div>
                </div>
                <div
                  className="px-2 py-1 rounded text-sm font-semibold ml-2"
                  style={{
                    backgroundColor: getConfidenceColor(item.confidence_score) + '20',
                    color: getConfidenceColor(item.confidence_score),
                  }}
                >
                  {Math.round(item.confidence_score)}%
                </div>
              </div>

              <div
                className="text-sm"
                style={{ color: themeParams.hint_color || '#6b7280' }}
              >
                {item.quantity} {item.unit} × {item.price.toLocaleString('ru-RU')} ₸
                = {item.total.toLocaleString('ru-RU')} ₸
              </div>
            </div>
          ))}
        </div>

        <div
          className="p-4 rounded-lg border-t-4"
          style={{
            backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
            borderColor: themeParams.link_color || '#3b82f6',
          }}
        >
          <div className="flex justify-between items-center mb-2">
            <span
              className="font-semibold"
              style={{ color: themeParams.text_color || '#000000' }}
            >
              Итого:
            </span>
            <span
              className="text-xl font-bold"
              style={{ color: themeParams.text_color || '#000000' }}
            >
              {supply.total_amount.toLocaleString('ru-RU')} ₸
            </span>
          </div>

          <div className="flex justify-between items-center">
            <span
              className="text-sm"
              style={{ color: themeParams.hint_color || '#6b7280' }}
            >
              Средняя точность:
            </span>
            <span
              className="font-semibold"
              style={{ color: getConfidenceColor(avgConfidence) }}
            >
              {Math.round(avgConfidence)}%
            </span>
          </div>

          {supply.poster_supply_id && (
            <div
              className="text-sm mt-2"
              style={{ color: themeParams.hint_color || '#6b7280' }}
            >
              📌 ID в Poster: #{supply.poster_supply_id}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

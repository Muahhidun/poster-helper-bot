import React from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram, useHaptic } from '../hooks/useTelegram'
import type { Supply } from '../types'

interface SupplyCardProps {
  supply: Supply
}

export const SupplyCard: React.FC<SupplyCardProps> = ({ supply }) => {
  const navigate = useNavigate()
  const { themeParams } = useTelegram()
  const haptic = useHaptic()

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleString('ru-RU', {
      day: 'numeric',
      month: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const getConfidenceEmoji = (confidence: number) => {
    if (confidence >= 90) return '✅'
    if (confidence >= 75) return '⚠️'
    return '❌'
  }

  const handleClick = () => {
    haptic.selection()
    navigate(`/supplies/${supply.id}`)
  }

  return (
    <div
      onClick={handleClick}
      className="p-4 rounded-lg shadow-sm mb-3 cursor-pointer hover:shadow-md transition-shadow"
      style={{
        backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
      }}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex-1">
          <div
            className="font-semibold text-lg"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            🏪 {supply.supplier_name}
          </div>
          <div
            className="text-sm"
            style={{ color: themeParams.hint_color || '#6b7280' }}
          >
            {formatDate(supply.created_at)}
          </div>
        </div>
        <div className="text-2xl">{getConfidenceEmoji(supply.avg_confidence)}</div>
      </div>

      <div className="flex items-center gap-3 text-sm">
        <span style={{ color: themeParams.hint_color || '#6b7280' }}>
          📦 {supply.items_count} товаров
        </span>
        <span style={{ color: themeParams.hint_color || '#6b7280' }}>•</span>
        <span style={{ color: themeParams.hint_color || '#6b7280' }}>
          💰 {supply.total_amount.toLocaleString('ru-RU')} ₸
        </span>
        <span style={{ color: themeParams.hint_color || '#6b7280' }}>•</span>
        <span style={{ color: themeParams.hint_color || '#6b7280' }}>
          {Math.round(supply.avg_confidence)}%
        </span>
      </div>
    </div>
  )
}

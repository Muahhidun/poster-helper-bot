import React from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram, useHaptic } from '../hooks/useTelegram'
import type { Alias } from '../types'

interface AliasCardProps {
  alias: Alias
  onDelete?: (id: number) => void
}

export const AliasCard: React.FC<AliasCardProps> = ({ alias, onDelete }) => {
  const navigate = useNavigate()
  const { themeParams } = useTelegram()
  const haptic = useHaptic()

  const handleEdit = () => {
    haptic.selection()
    navigate(`/aliases/${alias.id}/edit`)
  }

  const handleDelete = () => {
    haptic.impact('heavy')
    if (onDelete && confirm(`Удалить alias "${alias.alias_text}"?`)) {
      onDelete(alias.id)
    }
  }

  return (
    <div
      className="p-4 rounded-lg shadow-sm mb-3"
      style={{
        backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
      }}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div
            className="font-medium mb-1"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            {alias.alias_text}
          </div>
          <div
            className="text-sm mb-1"
            style={{ color: themeParams.link_color || '#3b82f6' }}
          >
            → {alias.poster_item_name}
          </div>
          <div
            className="text-xs"
            style={{ color: themeParams.hint_color || '#6b7280' }}
          >
            {alias.source}
            {alias.notes && ` • ${alias.notes}`}
          </div>
        </div>

        <div className="flex gap-2 ml-4">
          <button
            onClick={handleEdit}
            className="text-xl p-2"
            title="Редактировать"
          >
            ✏️
          </button>
          {onDelete && (
            <button
              onClick={handleDelete}
              className="text-xl p-2"
              title="Удалить"
            >
              🗑️
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

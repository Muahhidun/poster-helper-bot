import React from 'react'
import { useTelegram } from '../hooks/useTelegram'

interface StatCardProps {
  title: string
  value: string | number
  icon: string
}

export const StatCard: React.FC<StatCardProps> = ({ title, value, icon }) => {
  const { themeParams } = useTelegram()

  return (
    <div
      className="p-4 rounded-lg shadow-sm"
      style={{
        backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
      }}
    >
      <div className="text-2xl mb-2">{icon}</div>
      <div
        className="text-2xl font-bold mb-1"
        style={{ color: themeParams.text_color || '#000000' }}
      >
        {value}
      </div>
      <div
        className="text-sm"
        style={{ color: themeParams.hint_color || '#6b7280' }}
      >
        {title}
      </div>
    </div>
  )
}

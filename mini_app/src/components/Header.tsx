import React from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'

interface HeaderProps {
  title: string
  showBack?: boolean
}

export const Header: React.FC<HeaderProps> = ({ title, showBack = false }) => {
  const navigate = useNavigate()
  const { themeParams } = useTelegram()

  return (
    <div
      className="sticky top-0 z-10 px-4 py-3 flex items-center justify-between border-b"
      style={{
        backgroundColor: themeParams.bg_color || '#ffffff',
        borderColor: themeParams.hint_color || '#e5e7eb',
      }}
    >
      <div className="flex items-center gap-3">
        {showBack && (
          <button
            onClick={() => navigate(-1)}
            className="text-2xl"
            style={{ color: themeParams.link_color || '#3b82f6' }}
          >
            ◀
          </button>
        )}
        <h1
          className="text-xl font-bold"
          style={{ color: themeParams.text_color || '#000000' }}
        >
          {title}
        </h1>
      </div>
    </div>
  )
}

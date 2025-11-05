import { useEffect, useState } from 'react'
import type { TelegramWebApp, TelegramUser } from '../types'

interface UseTelegramReturn {
  webApp: TelegramWebApp | null
  user: TelegramUser | null
  colorScheme: 'light' | 'dark'
  themeParams: TelegramWebApp['themeParams']
  isReady: boolean
}

export const useTelegram = (): UseTelegramReturn => {
  const [webApp, setWebApp] = useState<TelegramWebApp | null>(null)
  const [isReady, setIsReady] = useState(false)

  useEffect(() => {
    const app = window.Telegram?.WebApp
    if (app) {
      app.ready()
      app.expand()
      setWebApp(app)
      setIsReady(true)
    } else {
      console.warn('Telegram WebApp not available')
      setIsReady(true) // Allow development without Telegram
    }
  }, [])

  return {
    webApp,
    user: webApp?.initDataUnsafe?.user || null,
    colorScheme: webApp?.colorScheme || 'light',
    themeParams: webApp?.themeParams || {},
    isReady,
  }
}

// Helper hook for haptic feedback
export const useHaptic = () => {
  const { webApp } = useTelegram()

  return {
    impact: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft' = 'medium') => {
      webApp?.HapticFeedback.impactOccurred(style)
    },
    notification: (type: 'error' | 'success' | 'warning') => {
      webApp?.HapticFeedback.notificationOccurred(type)
    },
    selection: () => {
      webApp?.HapticFeedback.selectionChanged()
    },
  }
}

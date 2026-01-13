import React from 'react'
import { Link } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { StatCard } from '../components/StatCard'
import { Chart } from '../components/Chart'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'

export const Dashboard: React.FC = () => {
  const { user, themeParams } = useTelegram()

  const { data: dashboard, loading, error, refetch } = useApi(
    () => getApiClient().getDashboard()
  )

  if (loading) return <Loading />
  if (error) return <ErrorMessage message={error.message} onRetry={refetch} />
  if (!dashboard) return null

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen">
      <Header title="Poster Helper" />

      <div className="p-4">
        <h2
          className="text-xl font-semibold mb-4"
          style={{ color: themeParams.text_color || '#000000' }}
        >
          Добро пожаловать, {user?.first_name || 'Пользователь'}!
        </h2>

        <div className="mb-6">
          <h3
            className="text-lg font-semibold mb-3"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            Статистика за месяц
          </h3>
          <div className="grid grid-cols-3 gap-3">
            <StatCard
              title="Накладных"
              value={dashboard.supplies_count}
              icon="📦"
            />
            <StatCard
              title="Товаров"
              value={dashboard.items_count}
              icon="📋"
            />
            <StatCard
              title="Точность"
              value={`${Math.round(dashboard.avg_accuracy)}%`}
              icon="✅"
            />
          </div>
        </div>

        {dashboard.accuracy_trend && dashboard.accuracy_trend.length > 0 && (
          <div className="mb-6">
            <h3
              className="text-lg font-semibold mb-3"
              style={{ color: themeParams.text_color || '#000000' }}
            >
              Динамика точности
            </h3>
            <Chart data={dashboard.accuracy_trend} />
          </div>
        )}

        {dashboard.top_problematic && dashboard.top_problematic.length > 0 && (
          <div className="mb-6">
            <h3
              className="text-lg font-semibold mb-3"
              style={{ color: themeParams.text_color || '#000000' }}
            >
              Топ проблемных товаров
            </h3>
            {dashboard.top_problematic.map((item, idx) => (
              <div
                key={idx}
                className="mb-2 p-3 rounded-lg"
                style={{
                  backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
                  color: themeParams.text_color || '#000000',
                }}
              >
                {idx + 1}. {item.item} - {item.count} исправлений
              </div>
            ))}
          </div>
        )}

        <div className="grid grid-cols-2 gap-3 mb-6">
          <Link
            to="/supplies/new"
            className="p-4 rounded-lg text-center font-medium shadow-sm col-span-2"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            ➕ Новая поставка
          </Link>
          <Link
            to="/supplies"
            className="p-4 rounded-lg text-center font-medium shadow-sm"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            📦 История поставок
          </Link>
          <Link
            to="/templates"
            className="p-4 rounded-lg text-center font-medium shadow-sm"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            ⚡ Шаблоны
          </Link>
          <Link
            to="/aliases"
            className="p-4 rounded-lg text-center font-medium shadow-sm col-span-2"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            🏷️ Aliases
          </Link>
        </div>
      </div>
    </div>
  )
}

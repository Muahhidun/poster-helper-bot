import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTelegram, useHaptic } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { AliasCard } from '../components/AliasCard'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'

export const Aliases: React.FC = () => {
  const { themeParams } = useTelegram()
  const haptic = useHaptic()
  const [search, setSearch] = useState('')
  const [sourceFilter, setSourceFilter] = useState<string>('')

  const { data: response, loading, error, refetch } = useApi(
    () => getApiClient().getAliases(search, sourceFilter)
  )

  const handleDelete = async (id: number) => {
    try {
      haptic.notification('warning')
      await getApiClient().deleteAlias(id)
      haptic.notification('success')
      refetch()
    } catch (err) {
      haptic.notification('error')
      alert('Ошибка при удалении alias')
    }
  }

  if (loading && !response) return <Loading />
  if (error && !response) return <ErrorMessage message={error.message} onRetry={refetch} />
  if (!response) return null

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen">
      <Header title="Aliases" showBack />

      <div className="p-4">
        <div className="mb-4">
          <input
            type="text"
            placeholder="Поиск..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
              borderColor: themeParams.hint_color || '#e5e7eb',
              color: themeParams.text_color || '#000000',
            }}
          />
        </div>

        <div className="flex justify-between items-center mb-4">
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="px-3 py-2 rounded-lg border"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
              borderColor: themeParams.hint_color || '#e5e7eb',
              color: themeParams.text_color || '#000000',
            }}
          >
            <option value="">Все источники</option>
            <option value="user">Пользователь</option>
            <option value="auto">Авто</option>
            <option value="system">Система</option>
          </select>

          <Link
            to="/aliases/new"
            className="px-4 py-2 rounded-lg font-medium"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            + Добавить
          </Link>
        </div>

        {response.aliases.length > 0 ? (
          response.aliases.map((alias) => (
            <AliasCard key={alias.id} alias={alias} onDelete={handleDelete} />
          ))
        ) : (
          <div className="text-center py-12">
            <div className="text-6xl mb-4">🏷️</div>
            <div
              className="text-lg mb-4"
              style={{ color: themeParams.hint_color || '#6b7280' }}
            >
              {search || sourceFilter ? 'Ничего не найдено' : 'Пока нет aliases'}
            </div>
            {!search && !sourceFilter && (
              <Link
                to="/aliases/new"
                className="inline-block px-6 py-3 rounded-lg font-medium"
                style={{
                  backgroundColor: themeParams.button_color || '#3b82f6',
                  color: themeParams.button_text_color || '#ffffff',
                }}
              >
                Добавить первый alias
              </Link>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

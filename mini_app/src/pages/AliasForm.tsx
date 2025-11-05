import React, { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTelegram, useHaptic } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'
import type { PosterItem } from '../types'

export const AliasForm: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { themeParams } = useTelegram()
  const haptic = useHaptic()

  const isEdit = Boolean(id)

  const [aliasText, setAliasText] = useState('')
  const [posterItemId, setPosterItemId] = useState<number | null>(null)
  const [posterItemName, setPosterItemName] = useState('')
  const [source, setSource] = useState('user')
  const [notes, setNotes] = useState('')
  const [itemSearch, setItemSearch] = useState('')
  const [searchResults, setSearchResults] = useState<PosterItem[]>([])
  const [saving, setSaving] = useState(false)

  // Load existing alias if editing
  const { data: alias, loading, error } = useApi(
    async () => {
      if (isEdit && id) {
        const response = await getApiClient().getAliases()
        return response.aliases.find((a) => a.id === Number(id))
      }
      return null
    },
    { enabled: isEdit }
  )

  useEffect(() => {
    if (alias) {
      setAliasText(alias.alias_text)
      setPosterItemId(alias.poster_item_id)
      setPosterItemName(alias.poster_item_name)
      setSource(alias.source)
      setNotes(alias.notes || '')
    }
  }, [alias])

  // Search items
  useEffect(() => {
    if (itemSearch.length >= 2) {
      const timer = setTimeout(async () => {
        try {
          const results = await getApiClient().searchItems(itemSearch)
          setSearchResults(results)
        } catch (err) {
          console.error('Search error:', err)
        }
      }, 300)

      return () => clearTimeout(timer)
    } else {
      setSearchResults([])
    }
  }, [itemSearch])

  const handleSelectItem = (item: PosterItem) => {
    setPosterItemId(item.id)
    setPosterItemName(item.name)
    setItemSearch(item.name)
    setSearchResults([])
    haptic.selection()
  }

  const handleSave = async () => {
    if (!aliasText || !posterItemId || !posterItemName) {
      alert('Заполните все обязательные поля')
      haptic.notification('error')
      return
    }

    setSaving(true)
    haptic.impact('medium')

    try {
      if (isEdit && id) {
        await getApiClient().updateAlias(Number(id), {
          alias_text: aliasText,
          poster_item_id: posterItemId,
          poster_item_name: posterItemName,
          source,
          notes,
        })
      } else {
        await getApiClient().createAlias({
          alias_text: aliasText,
          poster_item_id: posterItemId,
          poster_item_name: posterItemName,
          source,
          notes,
        })
      }

      haptic.notification('success')
      navigate('/aliases')
    } catch (err) {
      haptic.notification('error')
      alert('Ошибка при сохранении')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!isEdit || !id) return

    if (confirm('Удалить этот alias?')) {
      try {
        await getApiClient().deleteAlias(Number(id))
        haptic.notification('success')
        navigate('/aliases')
      } catch (err) {
        haptic.notification('error')
        alert('Ошибка при удалении')
      }
    }
  }

  if (loading) return <Loading />
  if (error) return <ErrorMessage message={error.message} />

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen">
      <Header title={isEdit ? 'Редактировать Alias' : 'Новый Alias'} showBack />

      <div className="p-4">
        <div className="mb-4">
          <label
            className="block mb-2 font-medium"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            Alias текст *
          </label>
          <input
            type="text"
            value={aliasText}
            onChange={(e) => setAliasText(e.target.value)}
            placeholder="Например: Кола ПЭТ 1лх12"
            className="w-full px-4 py-2 rounded-lg border"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
              borderColor: themeParams.hint_color || '#e5e7eb',
              color: themeParams.text_color || '#000000',
            }}
          />
        </div>

        <div className="mb-4">
          <label
            className="block mb-2 font-medium"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            Источник
          </label>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
              borderColor: themeParams.hint_color || '#e5e7eb',
              color: themeParams.text_color || '#000000',
            }}
          >
            <option value="user">Пользователь</option>
            <option value="auto">Авто</option>
            <option value="system">Система</option>
          </select>
        </div>

        <div className="mb-4 relative">
          <label
            className="block mb-2 font-medium"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            Товар *
          </label>
          <input
            type="text"
            value={itemSearch || posterItemName}
            onChange={(e) => setItemSearch(e.target.value)}
            placeholder="Начните печатать..."
            className="w-full px-4 py-2 rounded-lg border"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
              borderColor: themeParams.hint_color || '#e5e7eb',
              color: themeParams.text_color || '#000000',
            }}
          />

          {searchResults.length > 0 && (
            <div
              className="absolute z-10 w-full mt-1 rounded-lg border shadow-lg max-h-60 overflow-y-auto"
              style={{
                backgroundColor: themeParams.bg_color || '#ffffff',
                borderColor: themeParams.hint_color || '#e5e7eb',
              }}
            >
              {searchResults.map((item) => (
                <div
                  key={`${item.type}-${item.id}`}
                  onClick={() => handleSelectItem(item)}
                  className="px-4 py-2 cursor-pointer hover:bg-gray-100"
                  style={{
                    color: themeParams.text_color || '#000000',
                  }}
                >
                  {item.name}
                  <span
                    className="text-xs ml-2"
                    style={{ color: themeParams.hint_color || '#6b7280' }}
                  >
                    ({item.type})
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="mb-6">
          <label
            className="block mb-2 font-medium"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            Примечание (опционально)
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Дополнительная информация..."
            rows={3}
            className="w-full px-4 py-2 rounded-lg border"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
              borderColor: themeParams.hint_color || '#e5e7eb',
              color: themeParams.text_color || '#000000',
            }}
          />
        </div>

        <div className="flex gap-3">
          {isEdit && (
            <button
              onClick={handleDelete}
              className="px-6 py-3 rounded-lg font-medium bg-red-500 text-white"
            >
              Удалить
            </button>
          )}

          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 px-6 py-3 rounded-lg font-medium disabled:opacity-50"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            {saving ? 'Сохранение...' : 'Сохранить'}
          </button>
        </div>
      </div>
    </div>
  )
}

import React, { useState } from 'react'
import { useTelegram, useHaptic } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'
import type { ShipmentTemplate, TemplateItem } from '../types'

interface TemplateCardProps {
  template: ShipmentTemplate
  onDelete: (templateName: string) => void
  onEdit: (template: ShipmentTemplate) => void
}

const TemplateCard: React.FC<TemplateCardProps> = ({ template, onDelete, onEdit }) => {
  const { themeParams } = useTelegram()
  const haptic = useHaptic()

  const handleDelete = () => {
    haptic.notification('warning')
    if (confirm(`Удалить шаблон "${template.template_name}"?`)) {
      onDelete(template.template_name)
    }
  }

  const handleEdit = () => {
    haptic.selection()
    onEdit(template)
  }

  return (
    <div
      className="rounded-lg p-4 mb-3 shadow-sm"
      style={{
        backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
        borderLeft: `4px solid ${themeParams.button_color || '#3b82f6'}`,
      }}
    >
      <div className="flex justify-between items-start mb-2">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-2xl">📦</span>
            <h3
              className="text-lg font-semibold capitalize"
              style={{ color: themeParams.text_color || '#000000' }}
            >
              {template.template_name}
            </h3>
          </div>
          <div className="text-sm space-y-1" style={{ color: themeParams.hint_color || '#6b7280' }}>
            <div>🏪 {template.supplier_name}</div>
            <div>💳 {template.account_name}</div>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleEdit}
            className="px-3 py-1 rounded text-sm font-medium"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            Изменить
          </button>
          <button
            onClick={handleDelete}
            className="px-3 py-1 rounded text-sm font-medium"
            style={{
              backgroundColor: '#ef4444',
              color: '#ffffff',
            }}
          >
            Удалить
          </button>
        </div>
      </div>

      <div className="mt-3 pt-3 border-t" style={{ borderColor: themeParams.hint_color || '#e5e7eb' }}>
        <div className="text-sm font-medium mb-2" style={{ color: themeParams.text_color || '#000000' }}>
          Товары:
        </div>
        {template.items.map((item, index) => (
          <div
            key={index}
            className="flex justify-between items-center py-1 text-sm"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            <span>{item.name}</span>
            <span className="font-mono font-medium">{item.price.toLocaleString()} ₸</span>
          </div>
        ))}
      </div>

      <div
        className="mt-3 pt-3 border-t text-sm"
        style={{ borderColor: themeParams.hint_color || '#e5e7eb', color: themeParams.hint_color || '#6b7280' }}
      >
        💡 Используйте: <code className="bg-gray-200 px-1 py-0.5 rounded">{template.template_name} &lt;количество&gt;</code>
      </div>
    </div>
  )
}

interface EditModalProps {
  template: ShipmentTemplate
  onSave: (templateName: string, updatedItems: TemplateItem[]) => void
  onClose: () => void
}

const EditModal: React.FC<EditModalProps> = ({ template, onSave, onClose }) => {
  const { themeParams } = useTelegram()
  const haptic = useHaptic()
  const [items, setItems] = useState<TemplateItem[]>([...template.items])

  const handlePriceChange = (index: number, newPrice: string) => {
    const price = parseFloat(newPrice)
    if (!isNaN(price) && price > 0) {
      const updatedItems = [...items]
      updatedItems[index] = { ...updatedItems[index], price }
      setItems(updatedItems)
    }
  }

  const handleSave = () => {
    haptic.notification('success')
    onSave(template.template_name, items)
  }

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50"
      onClick={onClose}
    >
      <div
        className="rounded-lg p-6 max-w-md w-full"
        style={{
          backgroundColor: themeParams.bg_color || '#ffffff',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          className="text-xl font-bold mb-4 capitalize"
          style={{ color: themeParams.text_color || '#000000' }}
        >
          Редактировать "{template.template_name}"
        </h2>

        <div className="space-y-3 mb-6">
          {items.map((item, index) => (
            <div key={index}>
              <label
                className="block text-sm font-medium mb-1"
                style={{ color: themeParams.text_color || '#000000' }}
              >
                {item.name}
              </label>
              <input
                type="number"
                value={item.price}
                onChange={(e) => handlePriceChange(index, e.target.value)}
                className="w-full px-3 py-2 rounded-lg border"
                placeholder="Цена"
                min="0"
                step="0.01"
                style={{
                  backgroundColor: themeParams.secondary_bg_color || '#f9fafb',
                  borderColor: themeParams.hint_color || '#e5e7eb',
                  color: themeParams.text_color || '#000000',
                }}
              />
            </div>
          ))}
        </div>

        <div className="flex gap-3">
          <button
            onClick={handleSave}
            className="flex-1 px-4 py-2 rounded-lg font-medium"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            Сохранить
          </button>
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 rounded-lg font-medium border"
            style={{
              borderColor: themeParams.hint_color || '#e5e7eb',
              color: themeParams.text_color || '#000000',
            }}
          >
            Отмена
          </button>
        </div>
      </div>
    </div>
  )
}

export const Templates: React.FC = () => {
  const { themeParams } = useTelegram()
  const haptic = useHaptic()
  const [editingTemplate, setEditingTemplate] = useState<ShipmentTemplate | null>(null)

  const { data: response, loading, error, refetch } = useApi(
    () => getApiClient().getTemplates()
  )

  const handleDelete = async (templateName: string) => {
    try {
      await getApiClient().deleteTemplate(templateName)
      haptic.notification('success')
      refetch()
    } catch (err) {
      haptic.notification('error')
      alert('Ошибка при удалении шаблона')
    }
  }

  const handleEdit = (template: ShipmentTemplate) => {
    setEditingTemplate(template)
  }

  const handleSave = async (templateName: string, updatedItems: TemplateItem[]) => {
    try {
      await getApiClient().updateTemplate(templateName, { items: updatedItems })
      haptic.notification('success')
      setEditingTemplate(null)
      refetch()
    } catch (err) {
      haptic.notification('error')
      alert('Ошибка при обновлении шаблона')
    }
  }

  if (loading && !response) return <Loading />
  if (error && !response) return <ErrorMessage message={error.message} onRetry={refetch} />
  if (!response) return null

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen">
      <Header title="Шаблоны поставок" showBack />

      <div className="p-4">
        {response.templates.length > 0 ? (
          <>
            <div className="mb-4 p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f9fafb' }}>
              <div className="text-sm" style={{ color: themeParams.hint_color || '#6b7280' }}>
                💡 Чтобы создать поставку, просто напишите боту:
                <br />
                <code className="bg-gray-200 px-2 py-1 rounded mt-1 inline-block">
                  {response.templates[0]?.template_name} 400
                </code>
              </div>
            </div>

            {response.templates.map((template) => (
              <TemplateCard
                key={template.template_name}
                template={template}
                onDelete={handleDelete}
                onEdit={handleEdit}
              />
            ))}
          </>
        ) : (
          <div className="text-center py-12">
            <div className="text-6xl mb-4">📦</div>
            <div
              className="text-lg mb-4"
              style={{ color: themeParams.hint_color || '#6b7280' }}
            >
              У вас пока нет шаблонов
            </div>
            <div
              className="text-sm max-w-md mx-auto"
              style={{ color: themeParams.hint_color || '#6b7280' }}
            >
              Создайте обычную поставку в боте и нажмите "💾 Сохранить как шаблон"
            </div>
          </div>
        )}
      </div>

      {editingTemplate && (
        <EditModal
          template={editingTemplate}
          onSave={handleSave}
          onClose={() => setEditingTemplate(null)}
        />
      )}
    </div>
  )
}

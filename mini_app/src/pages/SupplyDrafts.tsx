import { useState, useCallback } from 'react'
import { cn } from '@/lib/utils'
import {
  useSupplyDrafts,
  useUpdateSupplyDraft,
  useDeleteSupplyDraft,
  useUpdateSupplyDraftItem,
  useDeleteSupplyDraftItem,
  useCreateSupplyInPoster,
} from '@/hooks/useSupplyDrafts'
import type { SupplyDraft, SupplyDraftItem, PendingSupplyExpense, ExpensePosterAccount } from '@/types'

// Editable Cell Component
function EditableCell({
  value,
  type,
  onSave,
  placeholder,
  className,
}: {
  value: string | number
  type: 'text' | 'number'
  onSave: (value: string | number) => void
  placeholder?: string
  className?: string
}) {
  const [localValue, setLocalValue] = useState(value)
  const [isSaved, setIsSaved] = useState(false)

  const handleBlur = () => {
    if (localValue !== value) {
      onSave(localValue)
      setIsSaved(true)
      setTimeout(() => setIsSaved(false), 300)
    }
  }

  const handleFocus = () => {
    if (type === 'number' && localValue === 0) {
      setLocalValue('')
    }
  }

  return (
    <input
      type={type}
      value={localValue}
      onChange={(e) => setLocalValue(type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)}
      onBlur={handleBlur}
      onFocus={handleFocus}
      placeholder={placeholder}
      step={type === 'number' ? 0.01 : undefined}
      className={cn(
        'w-full px-2.5 py-1.5 border border-gray-200 rounded text-sm transition-all',
        'hover:border-gray-300 hover:bg-gray-50',
        'focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10',
        isSaved && 'animate-flash-save',
        className
      )}
    />
  )
}

// Supply Draft Item Row
function ItemRow({
  item,
  onUpdate,
  onDelete,
}: {
  item: SupplyDraftItem
  onUpdate: (itemId: number, field: string, value: string | number) => void
  onDelete: (itemId: number) => void
}) {
  const total = (item.quantity || 0) * (item.price || 0)

  return (
    <tr className="hover:bg-gray-50 transition-colors">
      <td className="px-3 py-2 border-b border-gray-100">
        <EditableCell
          value={item.ingredient_name}
          type="text"
          onSave={(v) => onUpdate(item.id, 'ingredient_name', v)}
          placeholder="Название..."
          className="w-40"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <EditableCell
          value={item.quantity}
          type="number"
          onSave={(v) => onUpdate(item.id, 'quantity', v)}
          className="w-20 text-right"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <EditableCell
          value={item.unit}
          type="text"
          onSave={(v) => onUpdate(item.id, 'unit', v)}
          className="w-16"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <EditableCell
          value={item.price}
          type="number"
          onSave={(v) => onUpdate(item.id, 'price', v)}
          className="w-24 text-right"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100 text-right font-medium tabular-nums">
        {total.toLocaleString('ru-RU')}₸
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <button
          type="button"
          onClick={() => onDelete(item.id)}
          className="p-1.5 text-sm opacity-40 hover:opacity-100 hover:bg-red-50 hover:text-red-600 rounded transition-all"
          title="Удалить"
        >
          🗑️
        </button>
      </td>
    </tr>
  )
}

// Supply Draft Card
function DraftCard({
  draft,
  pendingSupplies,
  posterAccounts,
  onUpdateDraft,
  onDeleteDraft,
  onUpdateItem,
  onDeleteItem,
  onCreateInPoster,
  isCreating,
}: {
  draft: SupplyDraft
  pendingSupplies: PendingSupplyExpense[]
  posterAccounts: ExpensePosterAccount[]
  onUpdateDraft: (id: number, field: string, value: string | number | null) => void
  onDeleteDraft: (id: number) => void
  onUpdateItem: (itemId: number, field: string, value: string | number) => void
  onDeleteItem: (itemId: number) => void
  onCreateInPoster: (draftId: number) => void
  isCreating: boolean
}) {
  const totalAmount = draft.items.reduce((sum, item) => sum + (item.quantity || 0) * (item.price || 0), 0)

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden shadow-sm mb-6">
      {/* Header */}
      <div className="px-5 py-4 bg-gradient-to-r from-blue-50 to-indigo-50 border-b border-gray-200">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="text-lg">📦</span>
            <EditableCell
              value={draft.supplier_name || ''}
              type="text"
              onSave={(v) => onUpdateDraft(draft.id, 'supplier_name', v as string)}
              placeholder="Поставщик..."
              className="w-48 font-medium"
            />
          </div>

          <div className="flex items-center gap-3">
            {/* Poster Account Select */}
            <select
              value={draft.poster_account_id || posterAccounts.find(pa => pa.is_primary)?.id || ''}
              onChange={(e) => onUpdateDraft(draft.id, 'poster_account_id', parseInt(e.target.value))}
              className="px-2.5 py-1.5 border border-gray-200 rounded text-sm bg-white cursor-pointer min-w-[120px]"
            >
              {posterAccounts.map(pa => (
                <option key={pa.id} value={pa.id}>{pa.name}</option>
              ))}
            </select>

            {/* Link to expense */}
            <select
              value={draft.linked_expense_draft_id || ''}
              onChange={(e) => onUpdateDraft(draft.id, 'linked_expense_draft_id', e.target.value ? parseInt(e.target.value) : null)}
              className="px-2.5 py-1.5 border border-gray-200 rounded text-sm bg-white cursor-pointer min-w-[150px]"
            >
              <option value="">-- Связать с расходом --</option>
              {pendingSupplies.map(ps => (
                <option key={ps.id} value={ps.id}>
                  {ps.description} ({ps.amount.toLocaleString('ru-RU')}₸)
                </option>
              ))}
            </select>

            <button
              onClick={() => onDeleteDraft(draft.id)}
              className="p-2 text-sm opacity-50 hover:opacity-100 hover:bg-red-50 hover:text-red-600 rounded transition-all"
              title="Удалить черновик"
            >
              🗑️
            </button>
          </div>
        </div>

        {draft.linked_expense_amount ? (
          <div className="mt-2 text-sm text-gray-600">
            💰 Связанный расход: <span className="font-medium">{draft.linked_expense_amount.toLocaleString('ru-RU')}₸</span>
          </div>
        ) : null}
      </div>

      {/* Items Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 text-xs font-semibold text-gray-500 uppercase tracking-wider">
              <th className="px-3 py-2.5 text-left">Наименование</th>
              <th className="px-3 py-2.5 text-left w-24">Кол-во</th>
              <th className="px-3 py-2.5 text-left w-20">Ед.</th>
              <th className="px-3 py-2.5 text-left w-28">Цена</th>
              <th className="px-3 py-2.5 text-right w-28">Сумма</th>
              <th className="px-3 py-2.5 w-10"></th>
            </tr>
          </thead>
          <tbody>
            {draft.items.map(item => (
              <ItemRow
                key={item.id}
                item={item}
                onUpdate={onUpdateItem}
                onDelete={onDeleteItem}
              />
            ))}
            {draft.items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-gray-400">
                  Нет позиций
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="px-5 py-3 bg-gray-50 flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-700">
          Итого: <span className="text-gray-900 tabular-nums">{totalAmount.toLocaleString('ru-RU')}₸</span>
        </span>

        <button
          onClick={() => onCreateInPoster(draft.id)}
          disabled={isCreating || draft.items.length === 0}
          className="px-4 py-2 bg-emerald-600 text-white rounded-md font-medium text-sm transition-all hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isCreating ? '⏳ Создание...' : '✅ Создать в Poster'}
        </button>
      </div>
    </div>
  )
}

// Main Supply Drafts Page
export function SupplyDrafts() {
  const { data, isLoading, error } = useSupplyDrafts()
  const updateDraftMutation = useUpdateSupplyDraft()
  const deleteDraftMutation = useDeleteSupplyDraft()
  const updateItemMutation = useUpdateSupplyDraftItem()
  const deleteItemMutation = useDeleteSupplyDraftItem()
  const createInPosterMutation = useCreateSupplyInPoster()

  const drafts = data?.drafts || []
  const pendingSupplies = data?.pending_supplies || []
  const posterAccounts = data?.poster_accounts || []

  const handleUpdateDraft = useCallback((id: number, field: string, value: string | number | null) => {
    updateDraftMutation.mutate({ id, data: { [field]: value } })
  }, [updateDraftMutation])

  const handleDeleteDraft = useCallback((id: number) => {
    if (!confirm('Удалить этот черновик поставки?')) return
    deleteDraftMutation.mutate(id)
  }, [deleteDraftMutation])

  const handleUpdateItem = useCallback((itemId: number, field: string, value: string | number) => {
    updateItemMutation.mutate({ itemId, data: { [field]: value } })
  }, [updateItemMutation])

  const handleDeleteItem = useCallback((itemId: number) => {
    if (!confirm('Удалить эту позицию?')) return
    deleteItemMutation.mutate(itemId)
  }, [deleteItemMutation])

  const handleCreateInPoster = useCallback(async (draftId: number) => {
    try {
      const result = await createInPosterMutation.mutateAsync(draftId)
      if (result.success) {
        alert('✅ Поставка создана в Poster')
      } else {
        alert(`❌ Ошибка: ${result.error || 'Неизвестная ошибка'}`)
      }
    } catch (e) {
      alert(`❌ Ошибка: ${e}`)
    }
  }, [createInPosterMutation])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">Загрузка...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-red-500">Ошибка загрузки данных</div>
      </div>
    )
  }

  return (
    <div className="max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-6 tracking-tight">Черновики поставок</h1>

      {/* Stats */}
      <div className="flex flex-wrap gap-5 mb-5 text-sm text-gray-500">
        <span>Черновиков сегодня: <strong className="text-gray-900">{drafts.length}</strong></span>
        <span>
          Ожидающих расходов (тип "поставка"): <strong className="text-gray-900">{pendingSupplies.length}</strong>
        </span>
      </div>

      {/* Drafts */}
      {drafts.map(draft => (
        <DraftCard
          key={draft.id}
          draft={draft}
          pendingSupplies={pendingSupplies}
          posterAccounts={posterAccounts}
          onUpdateDraft={handleUpdateDraft}
          onDeleteDraft={handleDeleteDraft}
          onUpdateItem={handleUpdateItem}
          onDeleteItem={handleDeleteItem}
          onCreateInPoster={handleCreateInPoster}
          isCreating={createInPosterMutation.isPending}
        />
      ))}

      {drafts.length === 0 && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-xl mb-2">📦</p>
          <p>Нет черновиков поставок на сегодня</p>
          <p className="mt-2 text-sm">Отправьте фото накладной в бота для создания черновика.</p>
        </div>
      )}

      {/* Flash animation style */}
      <style>{`
        @keyframes flash-save {
          0% { background-color: #d1fae5; }
          100% { background-color: transparent; }
        }
        .animate-flash-save {
          animation: flash-save 0.3s ease;
        }
      `}</style>
    </div>
  )
}

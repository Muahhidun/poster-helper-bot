import { useState, useCallback, useMemo, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import {
  useSupplyDrafts,
  useUpdateSupplyDraft,
  useDeleteSupplyDraft,
  useAddSupplyDraftItem,
  useUpdateSupplyDraftItem,
  useDeleteSupplyDraftItem,
  useCreateSupplyInPoster,
  useCreateSupplyDraft,
  useSearchIngredients,
} from '@/hooks/useSupplyDrafts'
import type { SupplyDraft, SupplyDraftItem, PendingSupplyExpense, ExpensePosterAccount, PosterItem } from '@/types'

// Helper function to evaluate math expressions safely (e.g., "2.5*12")
function evaluateExpression(expr: string): number | null {
  try {
    expr = expr.trim().replace(/\s/g, '').replace(/,/g, '.')
    if (!/^[\d+\-*/().]+$/.test(expr)) return null
    const result = new Function(`return ${expr}`)()
    return typeof result === 'number' && !isNaN(result) ? result : null
  } catch {
    return null
  }
}

// Ingredient Search Autocomplete Component
function IngredientSearchAutocomplete({
  posterAccountId,
  onSelect,
  disabled,
}: {
  posterAccountId: number | null
  onSelect: (ingredient: PosterItem) => void
  disabled?: boolean
}) {
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)

  // Debounce search query (100ms)
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 100)
    return () => clearTimeout(timer)
  }, [query])

  const { data: results = [] } = useSearchIngredients(
    debouncedQuery,
    debouncedQuery.length >= 1
  )

  // Sort results: same poster account first
  const sortedResults = useMemo(() => {
    return [...results].sort((a, b) => {
      const aMatch = a.poster_account_id === posterAccountId ? 0 : 1
      const bMatch = b.poster_account_id === posterAccountId ? 0 : 1
      return aMatch - bMatch
    }).slice(0, 15)
  }, [results, posterAccountId])

  const handleSelect = (ingredient: PosterItem) => {
    onSelect(ingredient)
    setQuery('')
    setIsOpen(false)
    setSelectedIndex(-1)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen || sortedResults.length === 0) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex(prev => Math.min(prev + 1, sortedResults.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex(prev => Math.max(prev - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (selectedIndex >= 0 && sortedResults[selectedIndex]) {
        handleSelect(sortedResults[selectedIndex])
      } else if (sortedResults.length === 1) {
        handleSelect(sortedResults[0])
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false)
    }
  }

  return (
    <div className="relative">
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setIsOpen(true); setSelectedIndex(-1) }}
        onFocus={() => setIsOpen(true)}
        onBlur={() => setTimeout(() => setIsOpen(false), 150)}
        onKeyDown={handleKeyDown}
        placeholder="Поиск ингредиента..."
        disabled={disabled}
        className={cn(
          'w-full px-3 py-2 border border-dashed border-gray-300 rounded text-sm bg-white/50',
          'placeholder:text-gray-400',
          'focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 focus:bg-white',
          'disabled:opacity-50'
        )}
      />
      {isOpen && sortedResults.length > 0 && (
        <div className="absolute bottom-full left-0 right-0 mb-1 bg-white border border-gray-200 rounded-md shadow-lg max-h-60 overflow-y-auto z-50">
          {sortedResults.map((ing, i) => (
            <div
              key={`${ing.id}-${ing.poster_account_id}`}
              className={cn(
                'px-3 py-2 cursor-pointer text-sm flex justify-between items-center',
                i === selectedIndex ? 'bg-blue-50' : 'hover:bg-gray-50'
              )}
              onMouseDown={() => handleSelect(ing)}
            >
              <span>{ing.name}</span>
              {ing.poster_account_name && (
                <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded ml-2">
                  {ing.poster_account_name}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// Smart Editable Cell with isFocused tracking (prevents reset while typing)
function EditableCell({
  value,
  type,
  onSave,
  placeholder,
  className,
  autoFocus,
}: {
  value: string | number
  type: 'text' | 'number'
  onSave: (value: string | number) => void
  placeholder?: string
  className?: string
  autoFocus?: boolean
}) {
  const [localValue, setLocalValue] = useState(value)
  const [isFocused, setIsFocused] = useState(false)
  const [isSaved, setIsSaved] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // Only sync from props when NOT focused
  useEffect(() => {
    if (!isFocused) {
      setLocalValue(value)
    }
  }, [value, isFocused])

  useEffect(() => {
    if (autoFocus && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [autoFocus])

  const handleBlur = () => {
    setIsFocused(false)
    // Evaluate math expressions for numbers
    let finalValue = localValue
    if (type === 'number' && typeof localValue === 'string') {
      const evaluated = evaluateExpression(localValue)
      if (evaluated !== null) {
        finalValue = evaluated
        setLocalValue(evaluated)
      }
    }
    if (finalValue !== value) {
      onSave(finalValue)
      setIsSaved(true)
      setTimeout(() => setIsSaved(false), 300)
    }
  }

  const handleFocus = () => {
    setIsFocused(true)
    if (type === 'number' && localValue === 0) {
      setLocalValue('')
    }
  }

  return (
    <input
      ref={inputRef}
      type={type === 'number' ? 'text' : type}
      inputMode={type === 'number' ? 'decimal' : undefined}
      value={localValue}
      onChange={(e) => setLocalValue(type === 'number' && e.target.value !== '' && !isNaN(Number(e.target.value.replace(',', '.')))
        ? e.target.value
        : type === 'number'
          ? e.target.value // Allow expressions like "2*5"
          : e.target.value)}
      onBlur={handleBlur}
      onFocus={handleFocus}
      placeholder={placeholder}
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

// Smart Item Row with auto-calculation
function SmartItemRow({
  item,
  onUpdate,
  onDelete,
  autoFocusQuantity,
}: {
  item: SupplyDraftItem
  onUpdate: (itemId: number, updates: Record<string, number>) => void
  onDelete: (itemId: number) => void
  autoFocusQuantity?: boolean
}) {
  const [localQty, setLocalQty] = useState<string>(String(item.quantity || ''))
  const [localPrice, setLocalPrice] = useState<string>(String(item.price || ''))
  const [localSum, setLocalSum] = useState<string>(String(Math.round((item.quantity || 0) * (item.price || 0))))
  const [isFocusedQty, setIsFocusedQty] = useState(false)
  const [isFocusedPrice, setIsFocusedPrice] = useState(false)
  const [isFocusedSum, setIsFocusedSum] = useState(false)
  const [lastEdited, setLastEdited] = useState<'qty' | 'price' | 'sum' | null>(null)
  const qtyRef = useRef<HTMLInputElement>(null)

  // Sync from props when not focused
  useEffect(() => {
    if (!isFocusedQty) setLocalQty(item.quantity ? String(item.quantity) : '')
  }, [item.quantity, isFocusedQty])

  useEffect(() => {
    if (!isFocusedPrice) setLocalPrice(item.price ? String(item.price) : '')
  }, [item.price, isFocusedPrice])

  useEffect(() => {
    if (!isFocusedSum) {
      const sum = (item.quantity || 0) * (item.price || 0)
      setLocalSum(sum ? String(Math.round(sum)) : '')
    }
  }, [item.quantity, item.price, isFocusedSum])

  useEffect(() => {
    if (autoFocusQuantity && qtyRef.current) {
      qtyRef.current.focus()
      qtyRef.current.select()
    }
  }, [autoFocusQuantity])

  const parseValue = (val: string): number => {
    const evaluated = evaluateExpression(val)
    return evaluated !== null ? evaluated : parseFloat(val.replace(',', '.')) || 0
  }

  const handleBlur = (field: 'qty' | 'price' | 'sum') => {
    if (field === 'qty') setIsFocusedQty(false)
    if (field === 'price') setIsFocusedPrice(false)
    if (field === 'sum') setIsFocusedSum(false)

    let qty = parseValue(localQty)
    let price = parseValue(localPrice)
    let sum = parseValue(localSum)

    // Smart calculation
    if (field === 'qty' || field === 'price') {
      setLastEdited(field)
      if (qty > 0 && price > 0) {
        sum = qty * price
        setLocalSum(String(Math.round(sum)))
      }
    } else if (field === 'sum') {
      // User edited sum - recalculate based on what's available
      if (sum > 0) {
        if (qty > 0 && lastEdited !== 'price') {
          // Recalculate price from sum and qty
          price = sum / qty
          setLocalPrice(String(Math.round(price * 100) / 100))
        } else if (price > 0) {
          // Recalculate qty from sum and price
          qty = sum / price
          setLocalQty(String(Math.round(qty * 1000) / 1000))
        }
      }
    }

    // Save to server if changed
    const updates: Record<string, number> = {}
    if (qty !== item.quantity) updates.quantity = qty
    if (price !== item.price) updates.price = price

    if (Object.keys(updates).length > 0) {
      onUpdate(item.id, updates)
    }
  }

  const displaySum = parseValue(localQty) * parseValue(localPrice)

  return (
    <tr className="hover:bg-gray-50 transition-colors">
      <td className="px-3 py-2 border-b border-gray-100">
        <span className="text-sm">{item.ingredient_name}</span>
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <input
          ref={qtyRef}
          type="text"
          inputMode="decimal"
          value={localQty}
          onChange={(e) => setLocalQty(e.target.value)}
          onFocus={() => { setIsFocusedQty(true); if (localQty === '0') setLocalQty('') }}
          onBlur={() => handleBlur('qty')}
          placeholder="0"
          className="w-20 px-2.5 py-1.5 border border-gray-200 rounded text-sm text-right tabular-nums focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <span className="text-sm text-gray-500">{item.unit || 'шт'}</span>
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <input
          type="text"
          inputMode="decimal"
          value={localPrice}
          onChange={(e) => setLocalPrice(e.target.value)}
          onFocus={() => { setIsFocusedPrice(true); if (localPrice === '0') setLocalPrice('') }}
          onBlur={() => handleBlur('price')}
          placeholder="0"
          className="w-24 px-2.5 py-1.5 border border-gray-200 rounded text-sm text-right tabular-nums focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <input
          type="text"
          inputMode="decimal"
          value={localSum}
          onChange={(e) => setLocalSum(e.target.value)}
          onFocus={() => { setIsFocusedSum(true); if (localSum === '0') setLocalSum('') }}
          onBlur={() => handleBlur('sum')}
          placeholder="0"
          className="w-24 px-2.5 py-1.5 border border-gray-200 rounded text-sm text-right tabular-nums focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100 text-right font-medium tabular-nums text-gray-600">
        {displaySum > 0 ? `${Math.round(displaySum).toLocaleString('ru-RU')}₸` : '—'}
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

// Empty Item Row for adding new items
function EmptyItemRow({
  draftId,
  posterAccountId,
  onAddItem,
  isAdding,
}: {
  draftId: number
  posterAccountId: number | null
  onAddItem: (draftId: number, ingredient: PosterItem) => void
  isAdding: boolean
}) {
  return (
    <tr className="bg-gray-50/50 hover:bg-gray-100/50 transition-colors">
      <td className="px-3 py-2 border-b border-gray-100" colSpan={2}>
        <IngredientSearchAutocomplete
          posterAccountId={posterAccountId}
          onSelect={(ing) => onAddItem(draftId, ing)}
          disabled={isAdding}
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <span className="text-gray-300 text-sm">шт</span>
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <input disabled placeholder="0" className="w-24 px-2.5 py-1.5 border border-dashed border-gray-300 rounded text-sm text-right opacity-40 bg-transparent" />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <input disabled placeholder="0" className="w-24 px-2.5 py-1.5 border border-dashed border-gray-300 rounded text-sm text-right opacity-40 bg-transparent" />
      </td>
      <td className="px-3 py-2 border-b border-gray-100 text-right font-medium tabular-nums text-gray-300">
        —
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        {isAdding && <span className="text-xs text-gray-400">⏳</span>}
      </td>
    </tr>
  )
}

// Supply Draft Card
function DraftCard({
  draft,
  pendingSupplies,
  posterAccounts,
  newlyAddedItemId,
  onUpdateDraft,
  onDeleteDraft,
  onUpdateItem,
  onDeleteItem,
  onAddItem,
  onCreateInPoster,
  isCreating,
  isAdding,
}: {
  draft: SupplyDraft
  pendingSupplies: PendingSupplyExpense[]
  posterAccounts: ExpensePosterAccount[]
  newlyAddedItemId: number | null
  onUpdateDraft: (id: number, field: string, value: string | number | null) => void
  onDeleteDraft: (id: number) => void
  onUpdateItem: (itemId: number, updates: Record<string, number>) => void
  onDeleteItem: (itemId: number) => void
  onAddItem: (draftId: number, ingredient: PosterItem) => void
  onCreateInPoster: (draftId: number) => void
  isCreating: boolean
  isAdding: boolean
}) {
  const totalAmount = draft.items.reduce((sum, item) => sum + (item.quantity || 0) * (item.price || 0), 0)
  const currentPosterAccountId = draft.poster_account_id || posterAccounts.find(pa => pa.is_primary)?.id || null

  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-sm mb-6">
      {/* Header */}
      <div className="px-5 py-4 bg-gradient-to-r from-blue-50 to-indigo-50 border-b border-gray-200 rounded-t-lg">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <span className="text-lg">📦</span>
            <EditableCell
              value={draft.supplier_name || ''}
              type="text"
              onSave={(v) => onUpdateDraft(draft.id, 'supplier_name', v as string)}
              placeholder="Поставщик..."
              className="w-48 font-medium"
            />
            <input
              type="date"
              value={draft.invoice_date || new Date().toISOString().split('T')[0]}
              onChange={(e) => onUpdateDraft(draft.id, 'invoice_date', e.target.value)}
              className="px-2.5 py-1.5 border border-gray-200 rounded text-sm bg-white cursor-pointer"
            />
          </div>

          <div className="flex items-center gap-3">
            {/* Poster Account Select */}
            <select
              value={currentPosterAccountId || ''}
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
              <th className="px-3 py-2.5 text-left w-16">Ед.</th>
              <th className="px-3 py-2.5 text-left w-28">Цена</th>
              <th className="px-3 py-2.5 text-left w-28">Сумма</th>
              <th className="px-3 py-2.5 text-right w-28">Итого</th>
              <th className="px-3 py-2.5 w-10"></th>
            </tr>
          </thead>
          <tbody>
            {draft.items.map(item => (
              <SmartItemRow
                key={item.id}
                item={item}
                onUpdate={onUpdateItem}
                onDelete={onDeleteItem}
                autoFocusQuantity={item.id === newlyAddedItemId}
              />
            ))}
            <EmptyItemRow
              draftId={draft.id}
              posterAccountId={currentPosterAccountId}
              onAddItem={onAddItem}
              isAdding={isAdding}
            />
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="px-5 py-3 bg-gray-50 flex items-center justify-between rounded-b-lg">
        <span className="text-sm font-semibold text-gray-700">
          Итого: <span className="text-gray-900 tabular-nums">{Math.round(totalAmount).toLocaleString('ru-RU')}₸</span>
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
  const addItemMutation = useAddSupplyDraftItem()
  const updateItemMutation = useUpdateSupplyDraftItem()
  const deleteItemMutation = useDeleteSupplyDraftItem()
  const createInPosterMutation = useCreateSupplyInPoster()
  const createDraftMutation = useCreateSupplyDraft()

  const [newlyAddedItemId, setNewlyAddedItemId] = useState<number | null>(null)

  const drafts = data?.drafts || []
  const pendingSupplies = data?.pending_supplies || []
  const posterAccounts = data?.poster_accounts || []

  const handleCreateDraft = useCallback(() => {
    createDraftMutation.mutate({})
  }, [createDraftMutation])

  const handleUpdateDraft = useCallback((id: number, field: string, value: string | number | null) => {
    updateDraftMutation.mutate({ id, data: { [field]: value } })
  }, [updateDraftMutation])

  const handleDeleteDraft = useCallback((id: number) => {
    if (!confirm('Удалить этот черновик поставки?')) return
    deleteDraftMutation.mutate(id)
  }, [deleteDraftMutation])

  const handleAddItem = useCallback(async (draftId: number, ingredient: PosterItem) => {
    try {
      const result = await addItemMutation.mutateAsync({
        draftId,
        data: {
          ingredient_id: ingredient.id,
          ingredient_name: ingredient.name,
          quantity: 0,
          price: 0,
          unit: 'шт',
          poster_account_id: ingredient.poster_account_id,
          storage_id: ingredient.storage_id,
          storage_name: ingredient.storage_name,
          item_type: ingredient.type || 'ingredient',
        },
      })
      if (result.id) {
        setNewlyAddedItemId(result.id)
        // Clear after a short delay
        setTimeout(() => setNewlyAddedItemId(null), 100)
      }
    } catch (e) {
      console.error('Failed to add item:', e)
    }
  }, [addItemMutation])

  const handleUpdateItem = useCallback((itemId: number, updates: Record<string, number>) => {
    updateItemMutation.mutate({ itemId, data: updates })
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
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Черновики поставок</h1>
        <button
          onClick={handleCreateDraft}
          disabled={createDraftMutation.isPending}
          className="px-4 py-2 bg-blue-600 text-white rounded-md font-medium text-sm transition-all hover:bg-blue-700 disabled:opacity-50"
        >
          {createDraftMutation.isPending ? '⏳ Создание...' : '➕ Добавить поставку'}
        </button>
      </div>

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
          newlyAddedItemId={newlyAddedItemId}
          onUpdateDraft={handleUpdateDraft}
          onDeleteDraft={handleDeleteDraft}
          onUpdateItem={handleUpdateItem}
          onDeleteItem={handleDeleteItem}
          onAddItem={handleAddItem}
          onCreateInPoster={handleCreateInPoster}
          isCreating={createInPosterMutation.isPending}
          isAdding={addItemMutation.isPending}
        />
      ))}

      {drafts.length === 0 && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-xl mb-2">📦</p>
          <p>Нет черновиков поставок на сегодня</p>
          <p className="mt-2 text-sm">Нажмите "Добавить поставку" или отправьте фото накладной в бота.</p>
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

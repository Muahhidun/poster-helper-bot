import { useState, useMemo, useCallback, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import {
  useExpenses,
  useUpdateExpense,
  useDeleteExpense,
  useCreateExpense,
  useSyncFromPoster,
  useProcessDrafts,
  useDeleteDrafts,
  useToggleExpenseType,
  usePosterTransactions,
  getCategoryDisplayName,
  getAccountType,
  findSyncStatus,
  buildAccountTypeMap,
} from '@/hooks/useExpenses'
import type { ExpenseDraft, ExpenseCategory, ExpensePosterAccount, PosterTransaction, ExpenseAccount } from '@/types'

type AccountType = 'cash' | 'kaspi' | 'halyk'
type SortDirection = 'asc' | 'desc' | null
type SortColumn = 'amount' | 'description' | 'type' | 'category' | 'department'

interface SortState {
  column: SortColumn | null
  direction: SortDirection
}

// Section configuration
const SECTIONS: { type: AccountType; label: string; icon: string; gradient: string }[] = [
  { type: 'cash', label: 'Наличка', icon: '💵', gradient: 'from-emerald-50 to-green-50 text-emerald-700' },
  { type: 'kaspi', label: 'Kaspi Pay', icon: '📱', gradient: 'from-orange-50 to-amber-50 text-orange-700' },
  { type: 'halyk', label: 'Халык', icon: '🏦', gradient: 'from-blue-50 to-sky-50 text-blue-700' },
]

// Editable Cell Component
function EditableCell({
  value,
  type,
  draftId,
  field,
  onSave,
  placeholder,
  className,
}: {
  value: string | number
  type: 'text' | 'number'
  draftId: number
  field: string
  onSave: (id: number, field: string, value: string | number) => void
  placeholder?: string
  className?: string
}) {
  const [localValue, setLocalValue] = useState(value)
  const [isSaved, setIsSaved] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setLocalValue(value)
  }, [value])

  const handleBlur = () => {
    if (localValue !== value) {
      onSave(draftId, field, localValue)
      setIsSaved(true)
      setTimeout(() => setIsSaved(false), 300)
    }
  }

  const handleFocus = () => {
    if (type === 'number' && localValue === 0) {
      setLocalValue('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      inputRef.current?.blur()
    }
  }

  return (
    <input
      ref={inputRef}
      type={type}
      value={localValue}
      onChange={(e) => setLocalValue(type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)}
      onBlur={handleBlur}
      onFocus={handleFocus}
      onKeyDown={handleKeyDown}
      placeholder={placeholder}
      step={type === 'number' ? 1 : undefined}
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

// Category Autocomplete Component
function CategoryAutocomplete({
  value,
  draftId,
  categories,
  posterAccountId,
  onSave,
}: {
  value: string
  draftId: number
  categories: ExpenseCategory[]
  posterAccountId: number | null
  onSave: (id: number, field: string, value: string) => void
}) {
  const [localValue, setLocalValue] = useState(value || '')
  const [isOpen, setIsOpen] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(-1)
  const [isSaved, setIsSaved] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setLocalValue(value || '')
  }, [value])

  const filteredCategories = useMemo(() => {
    const query = localValue.toLowerCase().trim()
    if (query.length < 1) return []

    let matches = categories.filter(c => {
      const displayName = getCategoryDisplayName(c.category_name)
      return displayName.toLowerCase().includes(query)
    })

    // Sort: selected department first
    matches.sort((a, b) => {
      const aMatch = a.poster_account_id === posterAccountId ? 0 : 1
      const bMatch = b.poster_account_id === posterAccountId ? 0 : 1
      if (aMatch !== bMatch) return aMatch - bMatch
      return getCategoryDisplayName(a.category_name).localeCompare(getCategoryDisplayName(b.category_name), 'ru')
    })

    // Deduplicate by display name
    const seen = new Set<string>()
    return matches.filter(c => {
      const displayName = getCategoryDisplayName(c.category_name).toLowerCase()
      if (seen.has(displayName)) return false
      seen.add(displayName)
      return true
    }).slice(0, 10)
  }, [localValue, categories, posterAccountId])

  const handleSelect = (categoryName: string) => {
    setLocalValue(categoryName)
    setIsOpen(false)
    onSave(draftId, 'category', categoryName)
    setIsSaved(true)
    setTimeout(() => setIsSaved(false), 300)
  }

  const handleBlur = () => {
    setTimeout(() => {
      setIsOpen(false)
      if (localValue !== value) {
        onSave(draftId, 'category', localValue)
      }
    }, 150)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex(prev => Math.min(prev + 1, filteredCategories.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex(prev => Math.max(prev - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (selectedIndex >= 0 && filteredCategories[selectedIndex]) {
        handleSelect(getCategoryDisplayName(filteredCategories[selectedIndex].category_name))
      } else if (filteredCategories.length === 1) {
        handleSelect(getCategoryDisplayName(filteredCategories[0].category_name))
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
        value={localValue}
        onChange={(e) => {
          setLocalValue(e.target.value)
          setIsOpen(true)
          setSelectedIndex(-1)
        }}
        onFocus={() => setIsOpen(true)}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        placeholder="Категория..."
        autoComplete="off"
        className={cn(
          'w-full px-2.5 py-1.5 border border-gray-200 rounded text-sm transition-all',
          'hover:border-gray-300 hover:bg-gray-50',
          'focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10',
          isSaved && 'animate-flash-save'
        )}
      />
      {isOpen && filteredCategories.length > 0 && (
        <div
          ref={resultsRef}
          className="absolute top-full left-0 right-0 mt-1 bg-white border border-gray-200 rounded-md shadow-lg max-h-60 overflow-y-auto z-50 min-w-[180px]"
        >
          {filteredCategories.map((cat, i) => {
            const displayName = getCategoryDisplayName(cat.category_name)
            const isOtherDept = posterAccountId && cat.poster_account_id && cat.poster_account_id !== posterAccountId
            return (
              <div
                key={`${cat.category_id}-${cat.poster_account_id}`}
                className={cn(
                  'px-3 py-2 cursor-pointer text-sm transition-colors',
                  i === selectedIndex ? 'bg-blue-50' : 'hover:bg-gray-50'
                )}
                onMouseDown={() => handleSelect(displayName)}
              >
                {displayName}
                {isOtherDept && cat.poster_account_name && (
                  <span className="ml-2 text-xs text-gray-400">({cat.poster_account_name})</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// Poster Account Select Component
function PosterAccountSelect({
  value,
  draftId,
  posterAccounts,
  onSave,
}: {
  value: number | null
  draftId: number
  posterAccounts: ExpensePosterAccount[]
  onSave: (id: number, field: string, value: number) => void
}) {
  const [isSaved, setIsSaved] = useState(false)

  const selectedValue = value || posterAccounts.find(pa => pa.is_primary)?.id || posterAccounts[0]?.id

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newValue = parseInt(e.target.value)
    onSave(draftId, 'poster_account_id', newValue)
    setIsSaved(true)
    setTimeout(() => setIsSaved(false), 300)
  }

  return (
    <select
      value={selectedValue}
      onChange={handleChange}
      className={cn(
        'px-2.5 py-1.5 border border-gray-200 rounded text-sm bg-white cursor-pointer min-w-[120px] transition-all',
        'hover:border-gray-300 hover:bg-gray-50',
        'focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10',
        isSaved && 'animate-flash-save'
      )}
    >
      {posterAccounts.map(pa => (
        <option key={pa.id} value={pa.id}>{pa.name}</option>
      ))}
    </select>
  )
}

// Type Toggle Button
function TypeButton({
  draft,
  onToggle,
}: {
  draft: ExpenseDraft
  onToggle: (id: number, newType: 'transaction' | 'supply') => void
}) {
  const getLabel = () => {
    if (draft.expense_type === 'supply') return '📦 поставка'
    if (draft.is_income) return '💵 доход'
    return '💰 транзакция'
  }

  const handleClick = () => {
    const newType = draft.expense_type === 'supply' ? 'transaction' : 'supply'
    onToggle(draft.id, newType)
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className="px-2.5 py-1 border border-gray-200 rounded-full bg-white text-xs whitespace-nowrap transition-all hover:bg-gray-50 hover:border-gray-300"
    >
      {getLabel()}
    </button>
  )
}

// Completion Status Button
function CompletionButton({
  status,
  draftId,
  onToggle,
}: {
  status: string
  draftId: number
  onToggle: (id: number, newStatus: 'pending' | 'partial' | 'completed') => void
}) {
  const statusIcons: Record<string, string> = {
    pending: '⚪',
    partial: '🟡',
    completed: '✅',
  }

  const handleClick = () => {
    const nextStatus = status === 'pending' ? 'partial' : status === 'partial' ? 'completed' : 'pending'
    onToggle(draftId, nextStatus as 'pending' | 'partial' | 'completed')
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className="p-1.5 text-lg rounded transition-transform hover:scale-110 hover:bg-gray-100"
      title="Статус выполнения"
    >
      {statusIcons[status] || '⚪'}
    </button>
  )
}

// Draft Row Component
function DraftRow({
  draft,
  categories,
  posterAccounts,
  accounts,
  posterTransactions,
  isSelected,
  onSelect,
  onUpdate,
  onDelete,
  onToggleType,
  onToggleCompletion,
}: {
  draft: ExpenseDraft
  categories: ExpenseCategory[]
  posterAccounts: ExpensePosterAccount[]
  accounts: ExpenseAccount[]
  posterTransactions: PosterTransaction[]
  isSelected: boolean
  onSelect: (id: number, selected: boolean) => void
  onUpdate: (id: number, field: string, value: string | number) => void
  onDelete: (id: number) => void
  onToggleType: (id: number, newType: 'transaction' | 'supply') => void
  onToggleCompletion: (id: number, newStatus: 'pending' | 'partial' | 'completed') => void
}) {
  const syncMatches = useMemo(() => {
    return findSyncStatus(
      draft.amount,
      draft.description,
      draft.category,
      draft.account_id,
      draft.poster_account_id,
      draft.expense_type,
      posterTransactions,
      accounts
    )
  }, [draft, posterTransactions, accounts])

  const rowClasses = cn(
    'draft-row transition-colors',
    draft.expense_type === 'supply' ? 'border-l-4 border-l-blue-500' :
      draft.is_income ? 'border-l-4 border-l-amber-400 bg-amber-50/50' : 'border-l-4 border-l-emerald-500',
    draft.completion_status === 'completed' && 'bg-emerald-50/70 opacity-70',
    draft.completion_status === 'partial' && 'bg-amber-50/70',
    syncMatches >= 4 && 'bg-emerald-50',
    syncMatches === 3 && 'bg-amber-50',
    'hover:bg-gray-50'
  )

  return (
    <tr className={rowClasses} data-id={draft.id}>
      <td className="px-3 py-2 border-b border-gray-100">
        <input
          type="checkbox"
          checked={isSelected}
          onChange={(e) => onSelect(draft.id, e.target.checked)}
          className="w-4 h-4 cursor-pointer accent-blue-600"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <CompletionButton
          status={draft.completion_status || 'pending'}
          draftId={draft.id}
          onToggle={onToggleCompletion}
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <EditableCell
          value={draft.amount}
          type="number"
          draftId={draft.id}
          field="amount"
          onSave={onUpdate}
          className="w-24 text-right font-semibold tabular-nums"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <EditableCell
          value={draft.description}
          type="text"
          draftId={draft.id}
          field="description"
          onSave={onUpdate}
          className="w-44"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <TypeButton draft={draft} onToggle={onToggleType} />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <CategoryAutocomplete
          value={draft.category || ''}
          draftId={draft.id}
          categories={categories}
          posterAccountId={draft.poster_account_id}
          onSave={onUpdate}
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <PosterAccountSelect
          value={draft.poster_account_id}
          draftId={draft.id}
          posterAccounts={posterAccounts}
          onSave={onUpdate}
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <button
          type="button"
          onClick={() => onDelete(draft.id)}
          className="p-1.5 text-sm opacity-40 hover:opacity-100 hover:bg-red-50 hover:text-red-600 rounded transition-all"
          title="Удалить"
        >
          🗑️
        </button>
      </td>
    </tr>
  )
}

// Section Component
function Section({
  type,
  label,
  icon,
  gradient,
  drafts,
  categories,
  posterAccounts,
  accounts,
  posterTransactions,
  selectedIds,
  sortState,
  onSelectAll,
  onSelect,
  onSort,
  onUpdate,
  onDelete,
  onToggleType,
  onToggleCompletion,
  onCreate,
  isCreating,
}: {
  type: AccountType
  label: string
  icon: string
  gradient: string
  drafts: ExpenseDraft[]
  categories: ExpenseCategory[]
  posterAccounts: ExpensePosterAccount[]
  accounts: ExpenseAccount[]
  posterTransactions: PosterTransaction[]
  selectedIds: Set<number>
  sortState: SortState
  onSelectAll: (type: AccountType, selected: boolean) => void
  onSelect: (id: number, selected: boolean) => void
  onSort: (type: AccountType, column: SortColumn) => void
  onUpdate: (id: number, field: string, value: string | number) => void
  onDelete: (id: number) => void
  onToggleType: (id: number, newType: 'transaction' | 'supply') => void
  onToggleCompletion: (id: number, newStatus: 'pending' | 'partial' | 'completed') => void
  onCreate: (type: AccountType) => void
  isCreating: boolean
}) {
  const allSelected = drafts.length > 0 && drafts.every(d => selectedIds.has(d.id))
  const sectionTotal = drafts.reduce((sum, d) => sum + (d.amount || 0), 0)

  const sortedDrafts = useMemo(() => {
    if (!sortState.column || !sortState.direction) return drafts

    const sorted = [...drafts].sort((a, b) => {
      let aVal: string | number = ''
      let bVal: string | number = ''

      switch (sortState.column) {
        case 'amount':
          aVal = a.amount || 0
          bVal = b.amount || 0
          break
        case 'description':
          aVal = (a.description || '').toLowerCase()
          bVal = (b.description || '').toLowerCase()
          break
        case 'type':
          aVal = a.expense_type
          bVal = b.expense_type
          break
        case 'category':
          aVal = (a.category || '').toLowerCase()
          bVal = (b.category || '').toLowerCase()
          break
        case 'department':
          aVal = posterAccounts.find(pa => pa.id === a.poster_account_id)?.name || ''
          bVal = posterAccounts.find(pa => pa.id === b.poster_account_id)?.name || ''
          break
      }

      let comparison: number
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        comparison = aVal - bVal
      } else {
        comparison = String(aVal).localeCompare(String(bVal), 'ru')
      }

      return sortState.direction === 'desc' ? -comparison : comparison
    })

    // Keep empty rows at the end
    const emptyRows = sorted.filter(d => !d.amount && !d.description?.trim())
    const dataRows = sorted.filter(d => d.amount || d.description?.trim())
    return [...dataRows, ...emptyRows]
  }, [drafts, sortState, posterAccounts])

  const renderSortIcon = (column: SortColumn) => {
    if (sortState.column !== column) return <span className="ml-1 text-gray-300 text-xs">↕</span>
    return <span className="ml-1 text-xs">{sortState.direction === 'asc' ? '↑' : '↓'}</span>
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden shadow-sm mb-6">
      <div className={cn(
        'px-5 py-3.5 flex items-center justify-between border-b border-gray-200 bg-gradient-to-r',
        gradient
      )}>
        <h2 className="text-sm font-semibold flex items-center gap-2">
          <span>{icon}</span>
          {label}
        </h2>
        <button
          onClick={() => onCreate(type)}
          disabled={isCreating}
          className="px-3 py-1.5 bg-white/80 text-gray-700 rounded-md text-xs font-medium transition-all hover:bg-white hover:shadow-sm disabled:opacity-50"
        >
          ➕ Добавить
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 text-xs font-semibold text-gray-500 uppercase tracking-wider">
              <th className="px-3 py-2.5 text-left w-10">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={(e) => onSelectAll(type, e.target.checked)}
                  className="w-4 h-4 cursor-pointer accent-blue-600"
                />
              </th>
              <th className="px-3 py-2.5 text-left w-10">✓</th>
              <th
                className="px-3 py-2.5 text-left cursor-pointer hover:bg-gray-100 transition-colors"
                onClick={() => onSort(type, 'amount')}
              >
                Сумма {renderSortIcon('amount')}
              </th>
              <th
                className="px-3 py-2.5 text-left cursor-pointer hover:bg-gray-100 transition-colors"
                onClick={() => onSort(type, 'description')}
              >
                Описание {renderSortIcon('description')}
              </th>
              <th
                className="px-3 py-2.5 text-left cursor-pointer hover:bg-gray-100 transition-colors"
                onClick={() => onSort(type, 'type')}
              >
                Тип {renderSortIcon('type')}
              </th>
              <th
                className="px-3 py-2.5 text-left cursor-pointer hover:bg-gray-100 transition-colors"
                onClick={() => onSort(type, 'category')}
              >
                Категория {renderSortIcon('category')}
              </th>
              <th
                className="px-3 py-2.5 text-left cursor-pointer hover:bg-gray-100 transition-colors"
                onClick={() => onSort(type, 'department')}
              >
                Отдел {renderSortIcon('department')}
              </th>
              <th className="px-3 py-2.5 text-left w-10"></th>
            </tr>
          </thead>
          <tbody>
            {sortedDrafts.map(draft => (
              <DraftRow
                key={draft.id}
                draft={draft}
                categories={categories}
                posterAccounts={posterAccounts}
                accounts={accounts}
                posterTransactions={posterTransactions}
                isSelected={selectedIds.has(draft.id)}
                onSelect={onSelect}
                onUpdate={onUpdate}
                onDelete={onDelete}
                onToggleType={onToggleType}
                onToggleCompletion={onToggleCompletion}
              />
            ))}
          </tbody>
        </table>
      </div>

      <div className="px-5 py-3 bg-gray-50 text-right text-sm font-semibold text-gray-600">
        <span className="text-gray-900 tabular-nums">{sectionTotal.toLocaleString('ru-RU')}₸</span>
      </div>
    </div>
  )
}

// Main Expenses Page
export function Expenses() {
  const { data, isLoading, error } = useExpenses()
  const { data: posterData } = usePosterTransactions()
  const updateMutation = useUpdateExpense()
  const deleteMutation = useDeleteExpense()
  const createMutation = useCreateExpense()
  const syncMutation = useSyncFromPoster()
  const processMutation = useProcessDrafts()
  const deleteDraftsMutation = useDeleteDrafts()
  const toggleTypeMutation = useToggleExpenseType()

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [sortStates, setSortStates] = useState<Record<AccountType, SortState>>({
    cash: { column: null, direction: null },
    kaspi: { column: null, direction: null },
    halyk: { column: null, direction: null },
  })

  const drafts = data?.drafts || []
  const categories = data?.categories || []
  const accounts = data?.accounts || []
  const posterAccounts = data?.poster_accounts || []
  const posterTransactions = posterData?.transactions || data?.poster_transactions || []

  // Group drafts by account type
  const groupedDrafts = useMemo(() => {
    const groups: Record<AccountType, ExpenseDraft[]> = {
      cash: [],
      kaspi: [],
      halyk: [],
    }

    drafts.forEach(draft => {
      const type = getAccountType(draft, accounts)
      groups[type].push(draft)
    })

    return groups
  }, [drafts, accounts])

  // Handlers
  const handleSelect = useCallback((id: number, selected: boolean) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (selected) {
        next.add(id)
      } else {
        next.delete(id)
      }
      return next
    })
  }, [])

  const handleSelectAll = useCallback((type: AccountType, selected: boolean) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      groupedDrafts[type].forEach(d => {
        if (selected) {
          next.add(d.id)
        } else {
          next.delete(d.id)
        }
      })
      return next
    })
  }, [groupedDrafts])

  const handleSort = useCallback((type: AccountType, column: SortColumn) => {
    setSortStates(prev => {
      const state = prev[type]
      let newDirection: SortDirection
      if (state.column !== column) {
        newDirection = 'asc'
      } else if (state.direction === 'asc') {
        newDirection = 'desc'
      } else if (state.direction === 'desc') {
        newDirection = null
      } else {
        newDirection = 'asc'
      }

      return {
        ...prev,
        [type]: {
          column: newDirection ? column : null,
          direction: newDirection,
        },
      }
    })
  }, [])

  const handleUpdate = useCallback((id: number, field: string, value: string | number) => {
    updateMutation.mutate({ id, data: { [field]: value } })
  }, [updateMutation])

  const handleDelete = useCallback((id: number) => {
    if (!confirm('Удалить этот черновик?')) return
    deleteMutation.mutate(id)
    setSelectedIds(prev => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }, [deleteMutation])

  const handleToggleType = useCallback((id: number, newType: 'transaction' | 'supply') => {
    toggleTypeMutation.mutate({ id, expenseType: newType })
  }, [toggleTypeMutation])

  const handleToggleCompletion = useCallback((id: number, newStatus: 'pending' | 'partial' | 'completed') => {
    updateMutation.mutate({ id, data: { completion_status: newStatus } })
  }, [updateMutation])

  // Build account type map to determine which account_id to use for each source type
  const accountTypeMap = useMemo(() => buildAccountTypeMap(accounts), [accounts])

  const handleCreate = useCallback((type: AccountType) => {
    // Determine default account_id based on section type
    const defaultAccountId = accountTypeMap[type]

    createMutation.mutate({
      amount: 0,
      description: '',
      expense_type: 'transaction',
      source: type,
      ...(defaultAccountId && { account_id: defaultAccountId }),
    })
  }, [createMutation, accountTypeMap])

  const handleSync = useCallback(async () => {
    const result = await syncMutation.mutateAsync()
    if (result.synced > 0) {
      alert(`✅ ${result.message}`)
    } else {
      alert('ℹ️ Новых транзакций не найдено.\nСтатусы совпадения обновлены.')
    }
  }, [syncMutation])

  const handleProcess = useCallback(async () => {
    if (selectedIds.size === 0) {
      alert('Выберите черновики для создания')
      return
    }
    await processMutation.mutateAsync(Array.from(selectedIds))
    setSelectedIds(new Set())
    alert('✅ Транзакции созданы в Poster')
  }, [selectedIds, processMutation])

  const handleDeleteSelected = useCallback(async () => {
    if (selectedIds.size === 0) {
      alert('Выберите черновики для удаления')
      return
    }
    if (!confirm(`Удалить ${selectedIds.size} выбранных?`)) return
    await deleteDraftsMutation.mutateAsync(Array.from(selectedIds))
    setSelectedIds(new Set())
  }, [selectedIds, deleteDraftsMutation])

  // Summary stats
  const stats = useMemo(() => {
    const transactions = drafts.filter(d => d.expense_type === 'transaction').length
    const supplies = drafts.filter(d => d.expense_type === 'supply').length
    const total = drafts.reduce((sum, d) => sum + (d.amount || 0), 0)
    return { total: drafts.length, transactions, supplies, sum: total }
  }, [drafts])

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
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-6 tracking-tight">Черновики расходов</h1>

      {/* Actions */}
      <div className="flex flex-wrap gap-3 mb-5 items-center">
        <button
          onClick={handleProcess}
          disabled={selectedIds.size === 0 || processMutation.isPending}
          className="px-4 py-2 bg-emerald-600 text-white rounded-md font-medium text-sm transition-all hover:bg-emerald-700 hover:-translate-y-0.5 hover:shadow-md disabled:opacity-50 disabled:cursor-not-allowed"
        >
          ✅ Создать выбранные транзакции
        </button>
        <button
          onClick={handleDeleteSelected}
          disabled={selectedIds.size === 0 || deleteDraftsMutation.isPending}
          className="px-4 py-2 bg-red-50 text-red-600 border border-red-200 rounded-md font-medium text-sm transition-all hover:bg-red-100 hover:border-red-300 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          🗑️ Удалить выбранные
        </button>
        <button
          onClick={handleSync}
          disabled={syncMutation.isPending}
          className="px-4 py-2 bg-gray-100 text-gray-700 rounded-md font-medium text-sm transition-all hover:bg-gray-200 disabled:opacity-50"
        >
          {syncMutation.isPending ? '⏳ Синхронизация...' : '🔄 Синхронизировать'}
        </button>
        <span className="ml-auto text-sm text-gray-500 font-medium">
          Выбрано: <span className="text-gray-900">{selectedIds.size}</span>
        </span>
      </div>

      {/* Sync Legend */}
      <div className="flex gap-5 mb-5 text-xs text-gray-500">
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-emerald-600"></span>
          Совпало 4/4 (уже в Poster)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-amber-500"></span>
          Совпало 3/4 (частичное)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-gray-200"></span>
          Не найдено в Poster
        </span>
      </div>

      {/* Sections */}
      {SECTIONS.map(section => (
        <Section
          key={section.type}
          type={section.type}
          label={section.label}
          icon={section.icon}
          gradient={section.gradient}
          drafts={groupedDrafts[section.type]}
          categories={categories}
          posterAccounts={posterAccounts}
          accounts={accounts}
          posterTransactions={posterTransactions}
          selectedIds={selectedIds}
          sortState={sortStates[section.type]}
          onSelectAll={handleSelectAll}
          onSelect={handleSelect}
          onSort={handleSort}
          onUpdate={handleUpdate}
          onDelete={handleDelete}
          onToggleType={handleToggleType}
          onToggleCompletion={handleToggleCompletion}
          onCreate={handleCreate}
          isCreating={createMutation.isPending}
        />
      ))}

      {/* Summary */}
      <div className="flex flex-wrap gap-8 items-center p-5 bg-gray-50 rounded-lg">
        <p className="text-sm text-gray-500">
          Всего: <span className="text-gray-900 font-medium">{stats.total}</span> записей
        </p>
        <p className="text-sm text-gray-500">
          💰 Транзакций: <span className="text-gray-900 font-medium">{stats.transactions}</span>
        </p>
        <p className="text-sm text-gray-500">
          📦 Поставок: <span className="text-gray-900 font-medium">{stats.supplies}</span>
        </p>
        <p className="text-sm">
          <strong className="text-gray-900">
            Общая сумма: <span className="tabular-nums">{stats.sum.toLocaleString('ru-RU')}</span>₸
          </strong>
        </p>
      </div>

      {drafts.length === 0 && (
        <div className="text-center py-16 text-gray-400">
          <p>Нет черновиков расходов</p>
          <p className="mt-2">Добавьте расход в нужную секцию или отправьте фото/Kaspi выписку в бота.</p>
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

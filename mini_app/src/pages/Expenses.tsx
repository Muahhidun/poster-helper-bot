import { useState, useMemo, useCallback, useRef, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { getApiClient } from '@/api/client'
// v2: Fixed sum calculation with Number() conversion for PostgreSQL DECIMAL
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
  useReconciliation,
  useSaveReconciliation,
  getCategoryDisplayName,
  getAccountType,
  findSyncStatus,
  buildAccountTypeMap,
} from '@/hooks/useExpenses'
import type { ExpenseDraft, ExpenseCategory, ExpensePosterAccount, PosterTransaction, ExpenseAccount, ReconciliationData, AccountTotals } from '@/types'

type AccountType = 'cash' | 'kaspi' | 'halyk'
type SortDirection = 'asc' | 'desc' | null
type SortColumn = 'status' | 'amount' | 'description' | 'type' | 'category' | 'department'

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
  const [isFocused, setIsFocused] = useState(false)
  const [isSaved, setIsSaved] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // Only sync from props when NOT focused (prevents reset while user is typing)
  useEffect(() => {
    if (!isFocused) {
      setLocalValue(value)
    }
  }, [value, isFocused])

  const handleBlur = () => {
    setIsFocused(false)
    if (localValue !== value) {
      onSave(draftId, field, localValue)
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
  const [isFocused, setIsFocused] = useState(false)
  const [isOpen, setIsOpen] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(-1)
  const [isSaved, setIsSaved] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)

  // Only sync from props when NOT focused
  useEffect(() => {
    if (!isFocused) {
      setLocalValue(value || '')
    }
  }, [value, isFocused])

  // Get all unique categories sorted by department
  const allCategories = useMemo(() => {
    const sorted = [...categories].sort((a, b) => {
      const aMatch = a.poster_account_id === posterAccountId ? 0 : 1
      const bMatch = b.poster_account_id === posterAccountId ? 0 : 1
      if (aMatch !== bMatch) return aMatch - bMatch
      return getCategoryDisplayName(a).localeCompare(getCategoryDisplayName(b), 'ru')
    })

    // Deduplicate by display name
    const seen = new Set<string>()
    return sorted.filter(c => {
      const displayName = getCategoryDisplayName(c).toLowerCase()
      if (!displayName || seen.has(displayName)) return false
      seen.add(displayName)
      return true
    })
  }, [categories, posterAccountId])

  // Filter categories based on input
  const filteredCategories = useMemo(() => {
    const query = localValue.toLowerCase().trim()

    // Show all categories when empty or when focused
    if (query.length < 1) return allCategories.slice(0, 15)

    return allCategories.filter(c => {
      const displayName = getCategoryDisplayName(c)
      return displayName.toLowerCase().includes(query)
    }).slice(0, 10)
  }, [localValue, allCategories])

  const handleSelect = (categoryName: string) => {
    setLocalValue(categoryName)
    setIsOpen(false)
    onSave(draftId, 'category', categoryName)
    setIsSaved(true)
    setTimeout(() => setIsSaved(false), 300)
  }

  const handleBlur = () => {
    setTimeout(() => {
      setIsFocused(false)
      setIsOpen(false)
      // Only save if value changed AND it's a valid category (or empty to clear)
      if (localValue !== value) {
        const isValidCategory = localValue === '' || allCategories.some(c =>
          getCategoryDisplayName(c).toLowerCase() === localValue.toLowerCase()
        )
        if (isValidCategory) {
          // Find the exact category name (with proper casing)
          const exactMatch = allCategories.find(c =>
            getCategoryDisplayName(c).toLowerCase() === localValue.toLowerCase()
          )
          const finalValue = exactMatch ? getCategoryDisplayName(exactMatch) : localValue
          setLocalValue(finalValue)
          onSave(draftId, 'category', finalValue)
        } else {
          // Reset to previous value if invalid
          setLocalValue(value || '')
        }
      }
    }, 150)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen || filteredCategories.length === 0) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex(prev => Math.min(prev + 1, filteredCategories.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex(prev => Math.max(prev - 1, 0))
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      if (selectedIndex >= 0 && filteredCategories[selectedIndex]) {
        e.preventDefault()
        handleSelect(getCategoryDisplayName(filteredCategories[selectedIndex]))
      } else if (filteredCategories.length === 1) {
        e.preventDefault()
        handleSelect(getCategoryDisplayName(filteredCategories[0]))
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false)
      setLocalValue(value || '')
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
        onFocus={() => { setIsFocused(true); setIsOpen(true); setSelectedIndex(-1) }}
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
            const displayName = getCategoryDisplayName(cat)
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

// Empty Draft Row Component (always at bottom, creates new draft on input)
function EmptyDraftRow({
  type,
  posterAccounts,
  categories,
  onCreate,
  isCreating,
}: {
  type: AccountType
  posterAccounts: ExpensePosterAccount[]
  categories: ExpenseCategory[]
  onCreate: (type: AccountType, data: { amount?: number; description?: string; category?: string }) => void
  isCreating: boolean
}) {
  const [localAmount, setLocalAmount] = useState<number | ''>('')
  const [localDescription, setLocalDescription] = useState('')
  const [localCategory, setLocalCategory] = useState('')
  const [showCategorySuggestions, setShowCategorySuggestions] = useState(false)
  const rowRef = useRef<HTMLTableRowElement>(null)
  const categoryInputRef = useRef<HTMLInputElement>(null)

  // Check if there's any data entered
  const hasData = (localAmount && localAmount > 0) || localDescription.trim() || localCategory.trim()

  // Submit handler - explicit submission via button or Enter key
  const handleSubmit = () => {
    if (isCreating || !hasData) return

    onCreate(type, {
      amount: localAmount || 0,
      description: localDescription,
      category: localCategory || undefined
    })
    setLocalAmount('')
    setLocalDescription('')
    setLocalCategory('')
  }

  // Handle Enter key to submit
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && hasData && !isCreating) {
      e.preventDefault()
      handleSubmit()
    }
  }

  // Category suggestions
  const uniqueCategories = useMemo(() => {
    const names = new Set<string>()
    categories.forEach(c => {
      const name = c.category_name || c.name
      if (name) names.add(name)
    })
    return Array.from(names).sort()
  }, [categories])

  const filteredCategories = useMemo(() => {
    if (!localCategory.trim()) return uniqueCategories.slice(0, 8)
    const lower = localCategory.toLowerCase()
    return uniqueCategories.filter(c => c.toLowerCase().includes(lower)).slice(0, 8)
  }, [localCategory, uniqueCategories])

  const defaultPosterAccountId = posterAccounts.find(pa => pa.is_primary)?.id || posterAccounts[0]?.id

  return (
    <tr ref={rowRef} className="draft-row border-l-[5px] border-l-gray-200 bg-gray-50/50 hover:bg-gray-100/50 transition-colors">
      <td className="px-3 py-2 border-b border-gray-100">
        <input type="checkbox" disabled className="w-4 h-4 opacity-30" />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <span className="p-1.5 text-lg opacity-30">⚪</span>
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <input
          type="number"
          value={localAmount}
          onChange={(e) => setLocalAmount(e.target.value === '' ? '' : Number(e.target.value))}
          onKeyDown={handleKeyDown}
          onFocus={() => localAmount === 0 && setLocalAmount('')}
          placeholder="0"
          disabled={isCreating}
          className="w-24 px-2.5 py-1.5 border border-dashed border-gray-300 rounded text-sm text-right font-semibold tabular-nums bg-white/50 placeholder:text-gray-300 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 focus:bg-white disabled:opacity-50"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <input
          type="text"
          value={localDescription}
          onChange={(e) => setLocalDescription(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Новая запись..."
          disabled={isCreating}
          className="w-44 px-2.5 py-1.5 border border-dashed border-gray-300 rounded text-sm bg-white/50 placeholder:text-gray-300 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 focus:bg-white disabled:opacity-50"
        />
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <span className="px-2.5 py-1 text-xs text-gray-400">💰 транзакция</span>
      </td>
      <td className="px-3 py-2 border-b border-gray-100 relative">
        <input
          ref={categoryInputRef}
          type="text"
          value={localCategory}
          onChange={(e) => setLocalCategory(e.target.value)}
          onFocus={() => setShowCategorySuggestions(true)}
          onBlur={() => setTimeout(() => setShowCategorySuggestions(false), 200)}
          onKeyDown={handleKeyDown}
          placeholder="Категория..."
          disabled={isCreating}
          className="w-full px-2.5 py-1.5 border border-dashed border-gray-300 rounded text-sm bg-white/50 placeholder:text-gray-300 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 focus:bg-white disabled:opacity-50"
        />
        {showCategorySuggestions && filteredCategories.length > 0 && (
          <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-50 max-h-48 overflow-auto">
            {filteredCategories.map((cat) => (
              <button
                key={cat}
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-gray-100 transition-colors"
                onMouseDown={(e) => {
                  e.preventDefault()
                  setLocalCategory(cat)
                  setShowCategorySuggestions(false)
                }}
              >
                {cat}
              </button>
            ))}
          </div>
        )}
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        <select
          disabled
          value={defaultPosterAccountId}
          className="px-2.5 py-1.5 border border-dashed border-gray-300 rounded text-sm bg-white/50 min-w-[120px] opacity-50"
        >
          {posterAccounts.map(pa => (
            <option key={pa.id} value={pa.id}>{pa.name}</option>
          ))}
        </select>
      </td>
      <td className="px-3 py-2 border-b border-gray-100">
        {isCreating ? (
          <span className="text-xs text-gray-400">⏳</span>
        ) : hasData ? (
          <button
            onClick={handleSubmit}
            className="p-1.5 text-green-600 hover:text-green-700 hover:bg-green-50 rounded transition-colors"
            title="Добавить запись"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
          </button>
        ) : null}
      </td>
    </tr>
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

  // Detect amount mismatch between website draft and Poster
  const posterAmountMismatch = useMemo(() => {
    if (draft.poster_amount == null) return false
    return Math.abs(draft.amount - draft.poster_amount) >= 0.01
  }, [draft.amount, draft.poster_amount])

  const rowClasses = cn(
    'draft-row transition-colors',
    draft.expense_type === 'supply' ? 'border-l-[5px] border-l-orange-500' :
      draft.is_income ? 'border-l-[5px] border-l-amber-400 bg-amber-50/50' : 'border-l-[5px] border-l-emerald-500',
    // Poster amount mismatch overrides completion status colors
    posterAmountMismatch ? 'bg-yellow-50/80 border-l-yellow-400' :
      draft.completion_status === 'completed' && 'bg-emerald-50/70 opacity-70',
    !posterAmountMismatch && draft.completion_status === 'partial' && 'bg-amber-50/70',
    !posterAmountMismatch && syncMatches >= 4 && 'bg-emerald-50',
    !posterAmountMismatch && syncMatches === 3 && 'bg-amber-50',
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
        <div className="flex flex-col items-end gap-0.5">
          <EditableCell
            value={draft.amount}
            type="number"
            draftId={draft.id}
            field="amount"
            onSave={onUpdate}
            className="w-24 text-right font-semibold tabular-nums"
          />
          {posterAmountMismatch && (
            <span
              className="text-[10px] text-yellow-700 bg-yellow-100 px-1.5 py-0.5 rounded-full whitespace-nowrap"
              title={`Сумма в Poster: ${draft.poster_amount}₸. Измените на сайте, чтобы совпало.`}
            >
              Poster: {draft.poster_amount}₸
            </span>
          )}
        </div>
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

// Reconciliation Header Component (баланс по источнику)
// - Cash: Fact input vs "Оставил в кассе" balance from Poster
// - Kaspi/Halyk: Fact input vs Poster income total (without showing Poster amount)
function ReconciliationHeader({
  type,
  data,
  posterTransactions,
  accounts,
  accountTotals,
  onSave,
}: {
  type: AccountType
  data: ReconciliationData | undefined
  posterTransactions: PosterTransaction[]
  accounts: ExpenseAccount[]
  accountTotals?: AccountTotals
  onSave: (source: AccountType, field: keyof ReconciliationData, value: number | null) => void
}) {
  // opening_balance is used to store the "fact" value user enters
  const [factValue, setFactValue] = useState<number | ''>(data?.opening_balance ?? '')

  // Sync from server data when it changes
  useEffect(() => {
    setFactValue(data?.opening_balance ?? '')
  }, [data])

  const saveField = (value: number | '') => {
    onSave(type, 'opening_balance', value === '' ? null : value)
  }

  const numericInputClass = cn(
    'w-28 px-2.5 py-1 border border-gray-200 rounded text-sm text-right font-semibold tabular-nums',
    'hover:border-gray-300 hover:bg-gray-50',
    'focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10'
  )

  // Calculate Poster total for this account type (income from transactions)
  const posterIncomeTotal = useMemo(() => {
    // Find account names that match this type
    const matchingAccountNames = accounts
      .filter(acc => {
        const name = (acc.name || '').toLowerCase()
        if (type === 'kaspi') {
          return name.includes('kaspi')
        }
        if (type === 'halyk') {
          return name.includes('халык') || name.includes('halyk')
        }
        if (type === 'cash') {
          return name.includes('оставил') || name.includes('закуп')
        }
        return false
      })
      .map(acc => (acc.name || '').toLowerCase())

    if (matchingAccountNames.length === 0) return null

    // Sum income transactions from Poster for these accounts (type=1 is income)
    const total = posterTransactions
      .filter(t => {
        if (t.type !== 1) return false // Only income
        const accountName = (t.account_name || '').toLowerCase()
        return matchingAccountNames.some(name =>
          accountName.includes(name) || name.includes(accountName)
        )
      })
      .reduce((sum, t) => sum + Math.abs(t.amount) / 100, 0)

    return total
  }, [type, posterTransactions, accounts])

  // For cash: use "Оставил в кассе" balance from accountTotals
  // For kaspi/halyk: use income total from transactions
  const posterValue = type === 'cash'
    ? (accountTotals?.cash ?? null)
    : posterIncomeTotal

  // Calculate difference: fact - poster
  const difference = useMemo(() => {
    if (factValue === '' || posterValue === null) return null
    return factValue - posterValue
  }, [factValue, posterValue])

  const diffColor = difference === null ? 'text-gray-500' :
    difference > 0.01 ? 'text-emerald-600' : difference < -0.01 ? 'text-red-600' : 'text-gray-500'

  // All sections now have the same layout: Факт input + Разница display
  return (
    <div className="px-5 py-2.5 bg-gray-50/80 border-b border-gray-200 flex flex-wrap items-center gap-4 text-sm">
      <label className="flex items-center gap-1.5 text-gray-500">
        Факт:
        <input
          type="number"
          value={factValue}
          onChange={e => setFactValue(e.target.value === '' ? '' : Number(e.target.value))}
          onBlur={() => saveField(factValue)}
          placeholder="0"
          className={numericInputClass}
        />
      </label>
      <span className="ml-auto text-gray-500">
        Разница:{' '}
        <span className={cn('font-semibold tabular-nums', diffColor)}>
          {difference !== null ? (
            <>
              {difference > 0.01 ? '+' : ''}{Math.abs(difference) < 0.01 ? '0' : difference.toLocaleString('ru-RU')}₸
            </>
          ) : '—'}
        </span>
      </span>
    </div>
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
  reconciliation,
  accountTotals,
  onSelectAll,
  onSelect,
  onSort,
  onUpdate,
  onDelete,
  onToggleType,
  onToggleCompletion,
  onCreate,
  isCreating,
  onReconciliationSave,
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
  reconciliation: ReconciliationData | undefined
  accountTotals?: AccountTotals
  onSelectAll: (type: AccountType, selected: boolean) => void
  onSelect: (id: number, selected: boolean) => void
  onSort: (type: AccountType, column: SortColumn) => void
  onUpdate: (id: number, field: string, value: string | number) => void
  onDelete: (id: number) => void
  onToggleType: (id: number, newType: 'transaction' | 'supply') => void
  onToggleCompletion: (id: number, newStatus: 'pending' | 'partial' | 'completed') => void
  onCreate: (type: AccountType, data: { amount?: number; description?: string; category?: string }) => void
  isCreating: boolean
  onReconciliationSave: (source: AccountType, field: keyof ReconciliationData, value: number | null) => void
}) {
  const allSelected = drafts.length > 0 && drafts.every(d => selectedIds.has(d.id))
  const sectionTotal = drafts.reduce((sum, d) => sum + Number(d.amount || 0), 0)

  const sortedDrafts = useMemo(() => {
    if (!sortState.column || !sortState.direction) return drafts

    const sorted = [...drafts].sort((a, b) => {
      let aVal: string | number = ''
      let bVal: string | number = ''

      switch (sortState.column) {
        case 'status': {
          const statusOrder: Record<string, number> = { pending: 0, partial: 1, completed: 2 }
          aVal = statusOrder[a.completion_status || 'pending'] ?? 0
          bVal = statusOrder[b.completion_status || 'pending'] ?? 0
          break
        }
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
      <h2 className={cn(
        'px-5 py-3.5 text-sm font-semibold flex items-center gap-2 border-b border-gray-200 bg-gradient-to-r',
        gradient
      )}>
        <span>{icon}</span>
        {label}
      </h2>

      <ReconciliationHeader
        type={type}
        data={reconciliation}
        posterTransactions={posterTransactions}
        accounts={accounts}
        accountTotals={accountTotals}
        onSave={onReconciliationSave}
      />

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
              <th
                className="px-3 py-2.5 text-left w-10 cursor-pointer hover:bg-gray-100 transition-colors"
                onClick={() => onSort(type, 'status')}
              >
                ✓ {renderSortIcon('status')}
              </th>
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
            <EmptyDraftRow
              type={type}
              posterAccounts={posterAccounts}
              categories={categories}
              onCreate={onCreate}
              isCreating={isCreating}
            />
          </tbody>
        </table>
      </div>

      <div className="px-5 py-3 bg-gray-50 text-right text-sm font-semibold text-gray-600">
        <span className="text-gray-900 tabular-nums">{sectionTotal.toLocaleString('ru-RU')}₸</span>
      </div>
    </div>
  )
}

// Get today's date in Kazakhstan time (UTC+5)
function getKazakhstanToday(): string {
  const now = new Date()
  const kzOffset = 5 * 60 // UTC+5 in minutes
  const kzTime = new Date(now.getTime() + (kzOffset + now.getTimezoneOffset()) * 60000)
  return kzTime.toISOString().slice(0, 10)
}

// Checklist item type
interface ChecklistItem {
  id: string
  label: string
  checked: boolean
}

// Default checklist items
const DEFAULT_CHECKLIST_ITEMS: ChecklistItem[] = [
  { id: 'kaspi_check', label: 'Kaspi сверен', checked: false },
  { id: 'halyk_check', label: 'Halyk сверен', checked: false },
  { id: 'cash_check', label: 'Наличка сверена', checked: false },
  { id: 'poster_check', label: 'Poster проверен', checked: false },
  { id: 'collection_done', label: 'Инкассация сделана', checked: false },
]

// Checklist View Component
function ChecklistView({
  selectedDate,
  reconciliationData,
  posterTransactions,
  accounts,
  accountTotals,
  onBack,
}: {
  selectedDate: string
  reconciliationData: { reconciliation?: Record<AccountType, ReconciliationData> } | undefined
  posterTransactions: PosterTransaction[]
  accounts: ExpenseAccount[]
  accountTotals?: AccountTotals
  onBack: () => void
}) {
  // Load checklist state from localStorage
  const getStorageKey = () => `checklist_${selectedDate}`

  const [checklistItems, setChecklistItems] = useState<ChecklistItem[]>(() => {
    const stored = localStorage.getItem(getStorageKey())
    if (stored) {
      try {
        return JSON.parse(stored)
      } catch {
        return DEFAULT_CHECKLIST_ITEMS
      }
    }
    return DEFAULT_CHECKLIST_ITEMS
  })

  // Custom note fields (user can type anything)
  const [notes, setNotes] = useState<Record<string, string>>(() => {
    const stored = localStorage.getItem(`${getStorageKey()}_notes`)
    if (stored) {
      try {
        return JSON.parse(stored)
      } catch {
        return {}
      }
    }
    return {}
  })

  // Save to localStorage when state changes
  useEffect(() => {
    localStorage.setItem(getStorageKey(), JSON.stringify(checklistItems))
  }, [checklistItems, selectedDate])

  useEffect(() => {
    localStorage.setItem(`${getStorageKey()}_notes`, JSON.stringify(notes))
  }, [notes, selectedDate])

  // Reload state when date changes
  useEffect(() => {
    const stored = localStorage.getItem(getStorageKey())
    if (stored) {
      try {
        setChecklistItems(JSON.parse(stored))
      } catch {
        setChecklistItems(DEFAULT_CHECKLIST_ITEMS)
      }
    } else {
      setChecklistItems(DEFAULT_CHECKLIST_ITEMS)
    }

    const notesStored = localStorage.getItem(`${getStorageKey()}_notes`)
    if (notesStored) {
      try {
        setNotes(JSON.parse(notesStored))
      } catch {
        setNotes({})
      }
    } else {
      setNotes({})
    }
  }, [selectedDate])

  const toggleItem = (id: string) => {
    setChecklistItems(prev => prev.map(item =>
      item.id === id ? { ...item, checked: !item.checked } : item
    ))
  }

  const updateNote = (key: string, value: string) => {
    setNotes(prev => ({ ...prev, [key]: value }))
  }

  // Calculate Poster income total for a source type (same logic as ReconciliationHeader)
  const getPosterIncomeTotal = useCallback((type: AccountType): number | null => {
    // Find account names that match this type
    const matchingAccountNames = accounts
      .filter(acc => {
        const name = (acc.name || '').toLowerCase()
        if (type === 'kaspi') {
          return name.includes('kaspi')
        }
        if (type === 'halyk') {
          return name.includes('халык') || name.includes('halyk')
        }
        if (type === 'cash') {
          return name.includes('оставил') || name.includes('закуп')
        }
        return false
      })
      .map(acc => (acc.name || '').toLowerCase())

    if (matchingAccountNames.length === 0) return null

    // Sum income transactions from Poster for these accounts (type=1 is income)
    const total = posterTransactions
      .filter(t => {
        if (t.type !== 1) return false // Only income
        const accountName = (t.account_name || '').toLowerCase()
        return matchingAccountNames.some(name =>
          accountName.includes(name) || name.includes(accountName)
        )
      })
      .reduce((sum, t) => sum + Math.abs(t.amount) / 100, 0)

    return total
  }, [accounts, posterTransactions])

  // Get differences from reconciliation data (calculated the same way as ReconciliationHeader)
  const getDifference = (source: AccountType): number | null => {
    const data = reconciliationData?.reconciliation?.[source]
    const factValue = data?.opening_balance
    if (factValue == null) return null

    // Get poster value based on source type
    const posterValue = source === 'cash'
      ? (accountTotals?.cash ?? null)
      : getPosterIncomeTotal(source)

    if (posterValue === null) return null
    return factValue - posterValue
  }

  // Format date for display
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
  }

  // Export to clipboard
  const handleExport = async () => {
    const lines: string[] = []
    lines.push(`Чеклист закрытия смены — ${formatDate(selectedDate)}`)
    lines.push('')

    // Sources with differences
    const sources: { key: AccountType; label: string }[] = [
      { key: 'kaspi', label: 'Kaspi' },
      { key: 'halyk', label: 'Halyk' },
      { key: 'cash', label: 'Наличка' },
    ]

    sources.forEach(({ key, label }) => {
      const diff = getDifference(key)
      const note = notes[key] || ''
      const diffStr = diff !== null ? `${diff >= 0 ? '+' : ''}${diff.toLocaleString('ru-RU')}₸` : '—'
      lines.push(`${label}: ${diffStr}${note ? ` (${note})` : ''}`)
    })

    lines.push('')
    lines.push('Проверки:')
    checklistItems.forEach(item => {
      lines.push(`${item.checked ? '✅' : '⬜'} ${item.label}`)
    })

    if (notes.general) {
      lines.push('')
      lines.push(`Заметки: ${notes.general}`)
    }

    const text = lines.join('\n')

    try {
      await navigator.clipboard.writeText(text)
      alert('Скопировано в буфер обмена!')
    } catch {
      // Fallback for mobile
      const textarea = document.createElement('textarea')
      textarea.value = text
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
      alert('Скопировано!')
    }
  }

  const sources: { key: AccountType; label: string; icon: string; gradient: string }[] = [
    { key: 'kaspi', label: 'Kaspi', icon: '📱', gradient: 'from-orange-50 to-amber-50 border-orange-200' },
    { key: 'halyk', label: 'Halyk', icon: '🏦', gradient: 'from-blue-50 to-sky-50 border-blue-200' },
    { key: 'cash', label: 'Наличка', icon: '💵', gradient: 'from-emerald-50 to-green-50 border-emerald-200' },
  ]

  return (
    <div className="max-w-lg mx-auto px-4 py-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={onBack}
          className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h1 className="text-xl font-bold text-gray-900">Чеклист закрытия</h1>
      </div>

      {/* Date */}
      <div className="text-center mb-6">
        <span className="inline-block px-4 py-2 bg-gray-100 rounded-lg text-sm font-medium text-gray-700">
          {formatDate(selectedDate)}
        </span>
      </div>

      {/* Source differences */}
      <div className="space-y-3 mb-6">
        {sources.map(({ key, label, icon, gradient }) => {
          const diff = getDifference(key)
          const diffColor = diff === null ? 'text-gray-500' :
            diff > 0 ? 'text-emerald-600' : diff < 0 ? 'text-red-600' : 'text-gray-600'

          return (
            <div
              key={key}
              className={cn(
                'p-4 rounded-xl border bg-gradient-to-r',
                gradient
              )}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="flex items-center gap-2 font-medium text-gray-800">
                  <span>{icon}</span>
                  {label}
                </span>
                <span className={cn('text-lg font-bold tabular-nums', diffColor)}>
                  {diff !== null ? (
                    <>{diff > 0 ? '+' : ''}{diff.toLocaleString('ru-RU')}₸</>
                  ) : '—'}
                </span>
              </div>
              <input
                type="text"
                value={notes[key] || ''}
                onChange={e => updateNote(key, e.target.value)}
                placeholder="Заметка..."
                className="w-full px-3 py-2 bg-white/70 border border-gray-200 rounded-lg text-sm placeholder:text-gray-400 focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-400/20"
              />
            </div>
          )
        })}
      </div>

      {/* Checklist items */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mb-6">
        <h3 className="px-4 py-3 bg-gray-50 font-medium text-gray-700 border-b border-gray-200">
          Проверки
        </h3>
        <div className="divide-y divide-gray-100">
          {checklistItems.map(item => (
            <label
              key={item.id}
              className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 transition-colors"
            >
              <input
                type="checkbox"
                checked={item.checked}
                onChange={() => toggleItem(item.id)}
                className="w-5 h-5 rounded border-gray-300 text-blue-600 focus:ring-blue-500 cursor-pointer"
              />
              <span className={cn(
                'text-gray-700',
                item.checked && 'line-through text-gray-400'
              )}>
                {item.label}
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* General notes */}
      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Общие заметки
        </label>
        <textarea
          value={notes.general || ''}
          onChange={e => updateNote('general', e.target.value)}
          placeholder="Дополнительные заметки..."
          rows={3}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm placeholder:text-gray-400 focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-400/20 resize-none"
        />
      </div>

      {/* Export button */}
      <button
        onClick={handleExport}
        className="w-full py-3 bg-blue-600 text-white rounded-xl font-medium text-sm hover:bg-blue-700 transition-colors flex items-center justify-center gap-2"
      >
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
        </svg>
        Скопировать чеклист
      </button>
    </div>
  )
}

// Main Expenses Page
export function Expenses() {
  const [searchParams, setSearchParams] = useSearchParams()
  const isChecklistView = searchParams.get('view') === 'checklist'

  const [selectedDate, setSelectedDate] = useState(getKazakhstanToday)

  const { data, isLoading, error } = useExpenses(selectedDate)
  const { data: posterData } = usePosterTransactions()
  const { data: reconciliationData } = useReconciliation(selectedDate)

  // Extract data
  const accounts = data?.accounts || []
  const posterTransactions = posterData?.transactions || data?.poster_transactions || []
  const accountTotals = data?.account_totals

  // Handle back from checklist view
  const handleBackFromChecklist = useCallback(() => {
    setSearchParams({})
  }, [setSearchParams])

  // Handle open checklist view
  const handleOpenChecklist = useCallback(() => {
    setSearchParams({ view: 'checklist' })
  }, [setSearchParams])

  // Render checklist view if param is set
  if (isChecklistView) {
    return (
      <ChecklistView
        selectedDate={selectedDate}
        reconciliationData={reconciliationData}
        posterTransactions={posterTransactions}
        accounts={accounts}
        accountTotals={accountTotals}
        onBack={handleBackFromChecklist}
      />
    )
  }
  const updateMutation = useUpdateExpense()
  const deleteMutation = useDeleteExpense()
  const createMutation = useCreateExpense()
  const syncMutation = useSyncFromPoster()
  const processMutation = useProcessDrafts()
  const deleteDraftsMutation = useDeleteDrafts()
  const toggleTypeMutation = useToggleExpenseType()
  const saveReconciliationMutation = useSaveReconciliation()

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [sortStates, setSortStates] = useState<Record<AccountType, SortState>>({
    cash: { column: null, direction: null },
    kaspi: { column: null, direction: null },
    halyk: { column: null, direction: null },
  })

  const queryClient = useQueryClient()

  // Auto-sync from Poster every 5 minutes (silent, no alerts)
  useEffect(() => {
    const AUTO_SYNC_INTERVAL = 5 * 60 * 1000 // 5 minutes
    const intervalId = setInterval(async () => {
      try {
        const client = getApiClient()
        const result = await client.syncExpensesFromPoster()
        if (result.synced > 0 || result.updated > 0) {
          // Refresh data silently
          queryClient.invalidateQueries({ queryKey: ['expenses'] })
          queryClient.invalidateQueries({ queryKey: ['poster-transactions'] })
          console.log(`[Auto-sync] ${result.message}`)
        }
      } catch (err) {
        console.error('[Auto-sync] Error:', err)
      }
    }, AUTO_SYNC_INTERVAL)

    return () => clearInterval(intervalId)
  }, [queryClient])

  const drafts = data?.drafts || []
  const categories = data?.categories || []
  const posterAccounts = data?.poster_accounts || []

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

  const handleReconciliationSave = useCallback((source: AccountType, field: keyof ReconciliationData, value: number | null) => {
    const current: ReconciliationData = reconciliationData?.reconciliation?.[source] || {
      opening_balance: null, closing_balance: null, total_difference: null, notes: null
    }
    saveReconciliationMutation.mutate({
      date: selectedDate,
      source,
      opening_balance: field === 'opening_balance' ? value : current.opening_balance,
      closing_balance: field === 'closing_balance' ? value : current.closing_balance,
      total_difference: field === 'total_difference' ? value : current.total_difference,
    })
  }, [selectedDate, reconciliationData, saveReconciliationMutation])

  // Build account type map to determine which account_id to use for each source type
  const accountTypeMap = useMemo(() => buildAccountTypeMap(accounts), [accounts])

  const handleCreate = useCallback((type: AccountType, data: { amount?: number; description?: string; category?: string }) => {
    // Determine default account_id based on section type
    const defaultAccountId = accountTypeMap[type]

    createMutation.mutate({
      amount: data.amount || 0,
      description: data.description || '',
      expense_type: 'transaction',
      source: type,
      ...(defaultAccountId && { account_id: defaultAccountId }),
      ...(data.category && { category: data.category }),
    })
  }, [createMutation, accountTypeMap])

  const handleSync = useCallback(async () => {
    const result = await syncMutation.mutateAsync()
    if (result.synced > 0 || result.updated > 0) {
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
    const total = drafts.reduce((sum, d) => sum + Number(d.amount || 0), 0)
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
      <div className="flex items-center gap-4 mb-6">
        <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Черновики расходов</h1>
        <input
          type="date"
          value={selectedDate}
          onChange={e => {
            setSelectedDate(e.target.value)
            setSelectedIds(new Set())
          }}
          className="px-3 py-1.5 border border-gray-300 rounded-md text-sm font-medium focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
        />
        {selectedDate !== getKazakhstanToday() && (
          <button
            onClick={() => { setSelectedDate(getKazakhstanToday()); setSelectedIds(new Set()) }}
            className="px-3 py-1.5 text-sm text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded-md transition-colors"
          >
            Сегодня
          </button>
        )}
      </div>

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
        <button
          onClick={handleOpenChecklist}
          className="px-4 py-2 bg-indigo-50 text-indigo-600 border border-indigo-200 rounded-md font-medium text-sm transition-all hover:bg-indigo-100 hover:border-indigo-300"
        >
          📋 Чеклист
        </button>
        <span className="ml-auto text-sm text-gray-500 font-medium">
          Выбрано: <span className="text-gray-900">{selectedIds.size}</span>
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
          reconciliation={reconciliationData?.reconciliation?.[section.type]}
          accountTotals={accountTotals}
          onSelectAll={handleSelectAll}
          onSelect={handleSelect}
          onSort={handleSort}
          onUpdate={handleUpdate}
          onDelete={handleDelete}
          onToggleType={handleToggleType}
          onToggleCompletion={handleToggleCompletion}
          onCreate={handleCreate}
          isCreating={createMutation.isPending}
          onReconciliationSave={handleReconciliationSave}
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

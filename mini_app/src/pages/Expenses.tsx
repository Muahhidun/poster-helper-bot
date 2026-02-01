import { useState, useMemo, useCallback } from 'react'
import {
  RefreshCw,
  Plus,
  Check,
  Trash2,
  Package,
  Receipt,
  ChevronDown,
  ChevronRight,
  Wallet,
  CreditCard,
  Building2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  useExpenses,
  useUpdateExpense,
  useCreateExpense,
  useDeleteExpense,
  useSyncFromPoster,
  useProcessDrafts,
} from '@/hooks/useExpenses'
import type { ExpenseDraft, ExpenseSource } from '@/types'

// Editable Cell Component
function EditableCell({
  value: initialValue,
  row,
  column,
  onSave,
}: {
  value: string | number
  row: { original: ExpenseDraft }
  column: string
  onSave: (id: number, field: string, value: string | number) => void
}) {
  const [value, setValue] = useState(initialValue)
  const [isEditing, setIsEditing] = useState(false)
  const [isSaved, setIsSaved] = useState(false)

  const handleBlur = () => {
    setIsEditing(false)
    if (value !== initialValue) {
      onSave(row.original.id, column, value)
      setIsSaved(true)
      setTimeout(() => setIsSaved(false), 600)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      ;(e.target as HTMLInputElement).blur()
    }
    if (e.key === 'Escape') {
      setValue(initialValue)
      setIsEditing(false)
    }
  }

  const isAmount = column === 'amount'

  return (
    <div
      className={cn(
        'relative rounded-lg transition-all',
        isSaved && 'save-flash'
      )}
    >
      {isEditing ? (
        <input
          type={isAmount ? 'number' : 'text'}
          value={value}
          onChange={(e) =>
            setValue(isAmount ? parseFloat(e.target.value) || 0 : e.target.value)
          }
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          autoFocus
          className={cn(
            'w-full bg-transparent border-none outline-none focus:ring-2 focus:ring-primary/30 rounded-lg px-2 py-1',
            isAmount && 'text-right font-semibold tabular-nums'
          )}
        />
      ) : (
        <div
          onClick={() => setIsEditing(true)}
          className={cn(
            'cursor-text px-2 py-1 rounded-lg hover:bg-muted/50 transition-colors',
            isAmount && 'text-right font-semibold tabular-nums'
          )}
        >
          {isAmount ? (
            <span>{Number(value).toLocaleString('ru-RU')} ₸</span>
          ) : (
            <span className={!value ? 'text-muted-foreground' : ''}>
              {value || 'Введите описание...'}
            </span>
          )}
        </div>
      )}
      {isSaved && (
        <div className="absolute right-2 top-1/2 -translate-y-1/2">
          <Check className="h-4 w-4 text-green-500" />
        </div>
      )}
    </div>
  )
}

// Status Badge Component
function StatusBadge({ status }: { status: string }) {
  return (
    <div
      className={cn(
        'w-3 h-3 rounded-full',
        status === 'completed' && 'bg-green-500',
        status === 'partial' && 'bg-amber-500',
        status === 'pending' && 'bg-gray-300'
      )}
      title={
        status === 'completed'
          ? 'В Poster'
          : status === 'partial'
          ? 'Частично'
          : 'Не в Poster'
      }
    />
  )
}

// Source Header Component
function SourceHeader({ source }: { source: ExpenseSource }) {
  const config = {
    cash: { icon: Wallet, label: 'Наличка', color: 'text-green-600' },
    kaspi: { icon: CreditCard, label: 'Kaspi Pay', color: 'text-amber-600' },
    halyk: { icon: Building2, label: 'Халык', color: 'text-blue-600' },
  }

  const { icon: Icon, label, color } = config[source]

  return (
    <div className={cn('flex items-center gap-2 py-3 px-4', color)}>
      <Icon className="h-5 w-5" />
      <span className="font-semibold text-base">{label}</span>
    </div>
  )
}

export function Expenses() {
  const { data, isLoading, error } = useExpenses()
  const updateExpense = useUpdateExpense()
  const createExpense = useCreateExpense()
  const deleteExpense = useDeleteExpense()
  const syncFromPoster = useSyncFromPoster()
  const processDrafts = useProcessDrafts()

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    new Set(['cash', 'kaspi', 'halyk'])
  )

  // Group expenses by source
  const groupedExpenses = useMemo(() => {
    if (!data?.drafts) return { cash: [], kaspi: [], halyk: [] }

    const groups: Record<ExpenseSource, ExpenseDraft[]> = {
      cash: [],
      kaspi: [],
      halyk: [],
    }

    data.drafts.forEach((draft) => {
      const source = draft.source || 'cash'
      if (groups[source]) {
        groups[source].push(draft)
      } else {
        groups.cash.push(draft)
      }
    })

    return groups
  }, [data?.drafts])

  // Handle cell save
  const handleSave = useCallback(
    (id: number, field: string, value: string | number) => {
      updateExpense.mutate({ id, data: { [field]: value } })
    },
    [updateExpense]
  )

  // Handle selection
  const toggleSelection = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  // Handle sync
  const handleSync = () => {
    syncFromPoster.mutate()
  }

  // Handle process
  const handleProcess = () => {
    if (selectedIds.size > 0) {
      processDrafts.mutate(Array.from(selectedIds))
      setSelectedIds(new Set())
    }
  }

  // Handle delete
  const handleDelete = (id: number) => {
    deleteExpense.mutate(id)
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }

  // Handle add new
  const handleAddNew = (source: ExpenseSource) => {
    createExpense.mutate({
      amount: 0,
      description: '',
      source,
    })
  }

  // Toggle group expansion
  const toggleGroup = (source: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(source)) {
        next.delete(source)
      } else {
        next.add(source)
      }
      return next
    })
  }

  // Calculate totals
  const totals = useMemo(() => {
    const result = { cash: 0, kaspi: 0, halyk: 0, total: 0 }
    Object.entries(groupedExpenses).forEach(([source, items]) => {
      const sum = items.reduce((acc, item) => {
        const multiplier = item.is_income ? 1 : -1
        return acc + item.amount * multiplier
      }, 0)
      result[source as ExpenseSource] = sum
      result.total += sum
    })
    return result
  }, [groupedExpenses])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 text-center text-destructive">
        Ошибка загрузки данных
      </div>
    )
  }

  return (
    <div className="p-4 md:p-6 space-y-4 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Расходы</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {data?.drafts.length || 0} записей за сегодня
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleSync}
            disabled={syncFromPoster.isPending}
          >
            <RefreshCw
              className={cn(
                'h-4 w-4 mr-2',
                syncFromPoster.isPending && 'animate-spin'
              )}
            />
            Синхронизировать
          </Button>

          {selectedIds.size > 0 && (
            <Button size="sm" onClick={handleProcess} disabled={processDrafts.isPending}>
              <Check className="h-4 w-4 mr-2" />
              Создать ({selectedIds.size})
            </Button>
          )}
        </div>
      </div>

      {/* Expense Groups */}
      <div className="space-y-4">
        {(['cash', 'kaspi', 'halyk'] as ExpenseSource[]).map((source) => {
          const items = groupedExpenses[source]
          const isExpanded = expandedGroups.has(source)
          const total = totals[source]

          return (
            <Card key={source} className="overflow-hidden">
              {/* Group Header */}
              <div
                className="flex items-center justify-between px-4 py-3 bg-muted/30 cursor-pointer hover:bg-muted/50 transition-colors"
                onClick={() => toggleGroup(source)}
              >
                <div className="flex items-center gap-2">
                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  )}
                  <SourceHeader source={source} />
                  <span className="text-muted-foreground text-sm">
                    ({items.length})
                  </span>
                </div>
                <div
                  className={cn(
                    'font-semibold tabular-nums',
                    total >= 0 ? 'text-green-600' : 'text-foreground'
                  )}
                >
                  {total >= 0 ? '+' : ''}
                  {total.toLocaleString('ru-RU')} ₸
                </div>
              </div>

              {/* Items */}
              {isExpanded && (
                <div className="divide-y divide-border">
                  {items.map((item) => (
                    <div
                      key={item.id}
                      className={cn(
                        'flex items-center gap-3 px-4 py-2 table-row-hover',
                        item.completion_status === 'completed' && 'bg-green-50/50',
                        item.completion_status === 'partial' && 'bg-amber-50/50',
                        item.is_income && 'bg-amber-50/30'
                      )}
                    >
                      {/* Checkbox */}
                      <input
                        type="checkbox"
                        checked={selectedIds.has(item.id)}
                        onChange={() => toggleSelection(item.id)}
                        className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                      />

                      {/* Status */}
                      <StatusBadge status={item.completion_status} />

                      {/* Amount */}
                      <div className="w-28 flex-shrink-0">
                        <EditableCell
                          value={item.amount}
                          row={{ original: item } }
                          column="amount"
                          onSave={handleSave}
                        />
                      </div>

                      {/* Description */}
                      <div className="flex-1 min-w-0">
                        <EditableCell
                          value={item.description}
                          row={{ original: item } }
                          column="description"
                          onSave={handleSave}
                        />
                      </div>

                      {/* Type Badge */}
                      <div
                        className={cn(
                          'flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium',
                          item.expense_type === 'supply'
                            ? 'bg-blue-100 text-blue-700'
                            : item.is_income
                            ? 'bg-amber-100 text-amber-700'
                            : 'bg-gray-100 text-gray-700'
                        )}
                      >
                        {item.expense_type === 'supply' ? (
                          <>
                            <Package className="h-3 w-3" />
                            <span className="hidden sm:inline">поставка</span>
                          </>
                        ) : item.is_income ? (
                          <>
                            <Receipt className="h-3 w-3" />
                            <span className="hidden sm:inline">доход</span>
                          </>
                        ) : (
                          <>
                            <Receipt className="h-3 w-3" />
                            <span className="hidden sm:inline">расход</span>
                          </>
                        )}
                      </div>

                      {/* Category */}
                      <div className="hidden md:block w-32 text-sm text-muted-foreground truncate">
                        {item.category || '—'}
                      </div>

                      {/* Delete */}
                      <button
                        onClick={() => handleDelete(item.id)}
                        className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))}

                  {/* Add New Row */}
                  <button
                    onClick={() => handleAddNew(source)}
                    className="w-full flex items-center gap-2 px-4 py-3 text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors"
                  >
                    <Plus className="h-4 w-4" />
                    <span className="text-sm">Добавить запись</span>
                  </button>
                </div>
              )}
            </Card>
          )
        })}
      </div>

      {/* Summary */}
      <Card className="p-4">
        <div className="flex items-center justify-between">
          <span className="font-medium">Итого за день:</span>
          <span
            className={cn(
              'text-xl font-bold tabular-nums',
              totals.total >= 0 ? 'text-green-600' : 'text-foreground'
            )}
          >
            {totals.total >= 0 ? '+' : ''}
            {totals.total.toLocaleString('ru-RU')} ₸
          </span>
        </div>
      </Card>
    </div>
  )
}

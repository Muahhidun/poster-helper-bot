import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getApiClient } from '@/api/client'
import type {
  ExpensesResponse,
  UpdateExpenseRequest,
  CreateExpenseRequest,
  SyncFromPosterResponse,
  ShiftReconciliationResponse,
  SaveReconciliationRequest,
  PosterTransaction,
  ExpenseAccount,
} from '@/types'

// Fetch all expenses with related data, filtered by date
export function useExpenses(date?: string) {
  return useQuery<ExpensesResponse>({
    queryKey: ['expenses', date],
    queryFn: async () => {
      const client = getApiClient()
      return client.getExpenses(date)
    },
  })
}

// Fetch shift reconciliation for a date
export function useReconciliation(date?: string) {
  return useQuery<ShiftReconciliationResponse>({
    queryKey: ['shift-reconciliation', date],
    queryFn: async () => {
      const client = getApiClient()
      return client.getShiftReconciliation(date)
    },
  })
}

// Save shift reconciliation
export function useSaveReconciliation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: SaveReconciliationRequest) => {
      const client = getApiClient()
      return client.saveShiftReconciliation(data)
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['shift-reconciliation', variables.date] })
    },
  })
}

// Fetch poster transactions for sync status
export function usePosterTransactions() {
  return useQuery<{ success: boolean; transactions: PosterTransaction[] }>({
    queryKey: ['poster-transactions'],
    queryFn: async () => {
      const response = await fetch('/api/poster-transactions')
      return response.json()
    },
    staleTime: 1000 * 60, // 1 minute
  })
}

// Helper: get the active expenses query key and data (there's only one at a time)
function getActiveExpensesQuery(queryClient: ReturnType<typeof useQueryClient>) {
  const queries = queryClient.getQueryCache().findAll({ queryKey: ['expenses'] })
  const active = queries[0]
  if (!active) return null
  return {
    queryKey: active.queryKey as readonly unknown[],
    data: active.state.data as ExpensesResponse | undefined,
  }
}

// Helper: optimistically update the active expenses query
function setActiveExpensesData(
  queryClient: ReturnType<typeof useQueryClient>,
  updater: (old: ExpensesResponse) => ExpensesResponse
) {
  const active = getActiveExpensesQuery(queryClient)
  if (active?.data) {
    queryClient.setQueryData(active.queryKey, updater(active.data))
  }
}

// Update expense field - with optimistic update for instant UI
export function useUpdateExpense() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      id,
      data,
    }: {
      id: number
      data: UpdateExpenseRequest
    }) => {
      const client = getApiClient()
      return client.updateExpense(id, data)
    },
    // Optimistic update - update UI immediately
    onMutate: async ({ id, data }) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: ['expenses'] })

      // Snapshot the previous value
      const active = getActiveExpensesQuery(queryClient)
      const previousData = active?.data

      // Optimistically update the cache
      setActiveExpensesData(queryClient, (old) => ({
        ...old,
        drafts: old.drafts.map(draft =>
          draft.id === id ? { ...draft, ...data } : draft
        ),
      }))

      return { previousData, queryKey: active?.queryKey }
    },
    // Rollback on error
    onError: (_err, _variables, context) => {
      if (context?.previousData && context?.queryKey) {
        queryClient.setQueryData(context.queryKey, context.previousData)
      }
    },
    // Always refetch after error or success to ensure consistency
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Create new expense - with optimistic update
export function useCreateExpense() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: CreateExpenseRequest) => {
      const client = getApiClient()
      return client.createExpense(data)
    },
    // Optimistic update - add temporary item immediately
    onMutate: async (data) => {
      await queryClient.cancelQueries({ queryKey: ['expenses'] })
      const active = getActiveExpensesQuery(queryClient)
      const previousData = active?.data

      // Create temporary draft with negative ID (will be replaced on success)
      const tempDraft = {
        id: -Date.now(), // Temporary negative ID
        amount: data.amount,
        description: data.description,
        expense_type: data.expense_type || 'transaction',
        category: data.category || null,
        source: data.source || 'cash',
        account_id: data.account_id || null,
        poster_account_id: data.poster_account_id || null,
        poster_transaction_id: null,
        completion_status: 'pending' as const,
        is_income: false,
        created_at: new Date().toISOString(),
        poster_amount: null,
      }

      setActiveExpensesData(queryClient, (old) => ({
        ...old, drafts: [tempDraft, ...old.drafts]
      }))

      return { previousData, queryKey: active?.queryKey }
    },
    onError: (_err, _variables, context) => {
      if (context?.previousData && context?.queryKey) {
        queryClient.setQueryData(context.queryKey, context.previousData)
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Delete expense - with optimistic update
export function useDeleteExpense() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: number) => {
      const client = getApiClient()
      return client.deleteExpense(id)
    },
    // Optimistic update - remove immediately
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ['expenses'] })
      const active = getActiveExpensesQuery(queryClient)
      const previousData = active?.data

      setActiveExpensesData(queryClient, (old) => ({
        ...old, drafts: old.drafts.filter(d => d.id !== id)
      }))

      return { previousData, queryKey: active?.queryKey }
    },
    onError: (_err, _variables, context) => {
      if (context?.previousData && context?.queryKey) {
        queryClient.setQueryData(context.queryKey, context.previousData)
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Sync from Poster
export function useSyncFromPoster() {
  const queryClient = useQueryClient()

  return useMutation<SyncFromPosterResponse>({
    mutationFn: async () => {
      const client = getApiClient()
      return client.syncExpensesFromPoster()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
      queryClient.invalidateQueries({ queryKey: ['poster-transactions'] })
    },
  })
}

// Process selected drafts (create in Poster)
export function useProcessDrafts() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (draftIds: number[]) => {
      const client = getApiClient()
      return client.processExpenseDrafts(draftIds)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Delete multiple drafts - with optimistic update
export function useDeleteDrafts() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (draftIds: number[]) => {
      const client = getApiClient()
      await Promise.all(draftIds.map(id => client.deleteExpense(id)))
      return { success: true }
    },
    // Optimistic update - remove all immediately
    onMutate: async (draftIds) => {
      await queryClient.cancelQueries({ queryKey: ['expenses'] })
      const active = getActiveExpensesQuery(queryClient)
      const previousData = active?.data

      setActiveExpensesData(queryClient, (old) => {
        const idsSet = new Set(draftIds)
        return { ...old, drafts: old.drafts.filter(d => !idsSet.has(d.id)) }
      })

      return { previousData, queryKey: active?.queryKey }
    },
    onError: (_err, _variables, context) => {
      if (context?.previousData && context?.queryKey) {
        queryClient.setQueryData(context.queryKey, context.previousData)
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Toggle expense type - with optimistic update
export function useToggleExpenseType() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      id,
      expenseType,
    }: {
      id: number
      expenseType: 'transaction' | 'supply'
    }) => {
      const client = getApiClient()
      return client.toggleExpenseType(id, expenseType)
    },
    // Optimistic update - toggle immediately
    onMutate: async ({ id, expenseType }) => {
      await queryClient.cancelQueries({ queryKey: ['expenses'] })
      const active = getActiveExpensesQuery(queryClient)
      const previousData = active?.data

      setActiveExpensesData(queryClient, (old) => ({
        ...old,
        drafts: old.drafts.map(draft =>
          draft.id === id ? { ...draft, expense_type: expenseType } : draft
        ),
      }))

      return { previousData, queryKey: active?.queryKey }
    },
    onError: (_err, _variables, context) => {
      if (context?.previousData && context?.queryKey) {
        queryClient.setQueryData(context.queryKey, context.previousData)
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Category name mapping (Poster system names to Russian)
const categoryNameMap: Record<string, string> = {
  'book_category_action_marketing': 'Маркетинг',
  'book_category_action_banking_services': 'Банковские услуги и комиссии',
  'book_category_action_household_expenses': 'Хозяйственные расходы',
  'book_category_action_rent': 'Аренда',
  'book_category_action_utility_bills': 'Коммунальные платежи',
  'book_category_action_labour_cost': 'Зарплата',
  'book_category_action_supplies': 'Поставки',
  'book_category_action_actualization': 'Актуализация',
}

// Handles both string and category object (like Flask: cat.category_name || cat.name)
export function getCategoryDisplayName(
  category: { category_name?: string; name?: string } | string | null | undefined
): string {
  if (!category) return ''

  // If passed a string directly (backward compatibility)
  if (typeof category === 'string') {
    return categoryNameMap[category] || category
  }

  // If passed a category object - check both fields (like Flask does)
  const rawName = category.category_name || category.name || ''
  if (!rawName) return ''
  return categoryNameMap[rawName] || rawName
}

// Build account type map from accounts
export function buildAccountTypeMap(accounts: ExpenseAccount[]): Record<string, number | null> {
  const map: Record<string, number | null> = {
    cash: null,
    kaspi: null,
    halyk: null,
  }

  accounts.forEach(acc => {
    const name = (acc.name || '').toLowerCase()
    if (name.includes('kaspi')) {
      if (!map.kaspi) map.kaspi = acc.account_id
    } else if (name.includes('халык') || name.includes('halyk')) {
      if (!map.halyk) map.halyk = acc.account_id
    } else if (name.includes('наличк') || name.includes('закуп') || name.includes('cash')) {
      if (!map.cash) map.cash = acc.account_id
    }
  })

  // Fallback for cash
  if (!map.cash) {
    const cashAcc = accounts.find(acc => {
      const name = (acc.name || '').toLowerCase()
      return !name.includes('kaspi') && !name.includes('халык') && !name.includes('halyk')
    })
    if (cashAcc) map.cash = cashAcc.account_id
  }

  return map
}

// Determine account type from draft
export function getAccountType(
  draft: { account_id: number | null; source?: string },
  accounts: ExpenseAccount[]
): 'cash' | 'kaspi' | 'halyk' {
  // PRIORITY: check source first (reliably set during sync from Poster)
  const source = (draft.source || '').toLowerCase()

  if (source === 'kaspi' || source.includes('kaspi')) {
    return 'kaspi'
  }
  if (source === 'halyk' || source.includes('halyk') || source.includes('халык')) {
    return 'halyk'
  }

  // Fallback: check account name if source = 'cash' or empty
  const accountName = accounts.find(a =>
    a.account_id === draft.account_id ||
    String(a.account_id) === String(draft.account_id)
  )?.name?.toLowerCase() || ''

  if (accountName.includes('kaspi')) {
    return 'kaspi'
  }
  if (accountName.includes('халык') || accountName.includes('halyk')) {
    return 'halyk'
  }

  return 'cash'
}

// Find sync status with Poster transactions
export function findSyncStatus(
  amount: number,
  description: string,
  category: string | null,
  accountId: number | null,
  posterAccountId: number | null,
  expenseType: string,
  posterTransactions: PosterTransaction[],
  accounts: ExpenseAccount[]
): number {
  const financeAccount = accounts.find(a => a.account_id === accountId)
  const accountName = financeAccount ? (financeAccount.name || '').toLowerCase() : ''
  const descLower = (description || '').toLowerCase().trim()

  // Special handling for supplies
  if (expenseType === 'supply') {
    return findSupplySyncStatus(amount, descLower, category, accountName, posterTransactions)
  }

  // Regular transaction matching
  let bestMatch = 0

  for (const t of posterTransactions) {
    // Filter by poster account if specified
    if (posterAccountId && t.poster_account_id !== posterAccountId) continue

    // Only check expenses and income (type 0 and 1)
    if (t.type !== 0 && t.type !== 1) continue

    let matches = 0

    // 1. Check amount (Poster stores in kopecks/tiyn, divide by 100)
    const tAmount = Math.abs(t.amount) / 100
    if (Math.abs(tAmount - amount) < 2) {
      matches++
    }

    // 2. Check comment/description
    const tComment = (t.comment || '').toLowerCase().trim()
    const tCategoryName = (t.category_name || '').toLowerCase().trim()

    const descMatches =
      (tComment && descLower && (tComment.includes(descLower) || descLower.includes(tComment))) ||
      (tCategoryName && descLower && (tCategoryName === descLower || tCategoryName.includes(descLower) || descLower.includes(tCategoryName)))
    if (descMatches) {
      matches++
    }

    // 3. Check category
    const catLower = (category || '').toLowerCase()
    if (tCategoryName && catLower && (tCategoryName.includes(catLower) || catLower.includes(tCategoryName))) {
      matches++
    }

    // 4. Check account
    const tAccountName = (t.account_name || '').toLowerCase()
    if (accountName && tAccountName && (tAccountName.includes(accountName) || accountName.includes(tAccountName))) {
      matches++
    }

    if (matches > bestMatch) {
      bestMatch = matches
    }

    // Early exit if full match
    if (matches === 4) break
  }

  return bestMatch
}

// Supply sync status (can be split across multiple accounts)
function findSupplySyncStatus(
  expenseAmount: number,
  supplierName: string,
  category: string | null,
  accountName: string,
  posterTransactions: PosterTransaction[]
): number {
  if (!supplierName) return 0

  // Find all transactions that look like supply transactions with this supplier
  const supplyTransactions = posterTransactions.filter(t => {
    if (t.type !== 0) return false

    const comment = (t.comment || '').toLowerCase()
    const categoryName = (t.category_name || '').toLowerCase()

    const isSupplyComment = (comment.includes('поставка') || comment.includes('накладная')) && comment.includes(supplierName)
    const hasSupplierInComment = comment.includes(supplierName)
    const isSupplyCategory = categoryName.includes('поставк') || categoryName.includes('товар')

    return isSupplyComment || (hasSupplierInComment && isSupplyCategory) || (hasSupplierInComment && comment.length < 50)
  })

  if (supplyTransactions.length === 0) return 0

  // Sum up all matching supply transaction amounts
  const totalSupplyAmount = supplyTransactions.reduce((sum, t) => {
    return sum + Math.abs(t.amount) / 100
  }, 0)

  // Check category matches
  const catLower = (category || '').toLowerCase()
  let categoryMatches = false
  for (const t of supplyTransactions) {
    const tCategoryName = (t.category_name || '').toLowerCase()
    if (tCategoryName && catLower && (tCategoryName.includes(catLower) || catLower.includes(tCategoryName))) {
      categoryMatches = true
      break
    }
    if (tCategoryName.includes('поставка') || tCategoryName.includes('товар')) {
      categoryMatches = true
      break
    }
  }

  // Check account matches
  let accountMatches = false
  for (const t of supplyTransactions) {
    const tAccountName = (t.account_name || '').toLowerCase()
    if (accountName && tAccountName && (tAccountName.includes(accountName) || accountName.includes(tAccountName))) {
      accountMatches = true
      break
    }
  }

  // Calculate match score
  let matches = 0

  // 1. Amount match
  if (Math.abs(totalSupplyAmount - expenseAmount) < 2) {
    matches++
  } else if (totalSupplyAmount > 0 && Math.abs(totalSupplyAmount - expenseAmount) < expenseAmount * 0.01) {
    matches++
  }

  // 2. Supplier name match (already filtered)
  matches++

  // 3. Category
  if (categoryMatches) matches++

  // 4. Account
  if (accountMatches) matches++

  return matches
}

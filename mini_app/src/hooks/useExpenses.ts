import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getApiClient } from '@/api/client'
import type {
  ExpensesResponse,
  UpdateExpenseRequest,
  CreateExpenseRequest,
  SyncFromPosterResponse,
} from '@/types'

// Fetch all expenses for today
export function useExpenses() {
  return useQuery<ExpensesResponse>({
    queryKey: ['expenses'],
    queryFn: async () => {
      const client = getApiClient()
      return client.getExpenses()
    },
  })
}

// Update expense
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Create new expense
export function useCreateExpense() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: CreateExpenseRequest) => {
      const client = getApiClient()
      return client.createExpense(data)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Delete expense
export function useDeleteExpense() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: number) => {
      const client = getApiClient()
      return client.deleteExpense(id)
    },
    onSuccess: () => {
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

// Toggle expense type (transaction <-> supply)
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

// Update completion status
export function useUpdateCompletionStatus() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      id,
      status,
    }: {
      id: number
      status: 'pending' | 'partial' | 'completed'
    }) => {
      const client = getApiClient()
      return client.updateExpenseCompletionStatus(id, status)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['expenses'] })
    },
  })
}

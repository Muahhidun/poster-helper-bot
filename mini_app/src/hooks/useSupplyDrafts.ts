import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { SupplyDraftsResponse, PosterItem, ExpenseSource } from '@/types'

// Fetch all supply drafts
export function useSupplyDrafts() {
  return useQuery<SupplyDraftsResponse>({
    queryKey: ['supply-drafts'],
    queryFn: async () => {
      const response = await fetch('/api/supply-drafts')
      if (!response.ok) throw new Error('Failed to fetch supply drafts')
      return response.json()
    },
  })
}

// Update supply draft
export function useUpdateSupplyDraft() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      id,
      data,
    }: {
      id: number
      data: {
        supplier_name?: string
        poster_account_id?: number
        linked_expense_draft_id?: number | null
        invoice_date?: string
        source?: ExpenseSource
      }
    }) => {
      const response = await fetch(`/api/supply-drafts/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!response.ok) throw new Error('Failed to update supply draft')
      return response.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['supply-drafts'] })
    },
  })
}

// Delete supply draft
export function useDeleteSupplyDraft() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (id: number) => {
      const response = await fetch(`/api/supply-drafts/${id}`, {
        method: 'DELETE',
      })
      if (!response.ok) throw new Error('Failed to delete supply draft')
      return response.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['supply-drafts'] })
    },
  })
}

// Add item to supply draft
export function useAddSupplyDraftItem() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      draftId,
      data,
    }: {
      draftId: number
      data: {
        ingredient_id: number
        ingredient_name: string
        quantity: number
        price: number
        unit: string
        poster_account_id?: number
        storage_id?: number
        storage_name?: string
        item_type?: string
      }
    }) => {
      const response = await fetch(`/api/supply-drafts/${draftId}/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!response.ok) throw new Error('Failed to add item')
      return response.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['supply-drafts'] })
    },
  })
}

// Update supply draft item
export function useUpdateSupplyDraftItem() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      itemId,
      data,
    }: {
      itemId: number
      data: {
        ingredient_id?: number
        ingredient_name?: string
        quantity?: number
        price?: number
        unit?: string
        poster_account_id?: number
      }
    }) => {
      const response = await fetch(`/api/supply-drafts/items/${itemId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!response.ok) throw new Error('Failed to update item')
      return response.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['supply-drafts'] })
    },
  })
}

// Delete supply draft item
export function useDeleteSupplyDraftItem() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (itemId: number) => {
      const response = await fetch(`/api/supply-drafts/items/${itemId}`, {
        method: 'DELETE',
      })
      if (!response.ok) throw new Error('Failed to delete item')
      return response.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['supply-drafts'] })
    },
  })
}

// Create supply in Poster
export function useCreateSupplyInPoster() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (draftId: number) => {
      const response = await fetch(`/api/supply-drafts/${draftId}/create`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error('Failed to create supply')
      return response.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['supply-drafts'] })
    },
  })
}

// Create new empty supply draft
export function useCreateSupplyDraft() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data?: {
      supplier_name?: string
      poster_account_id?: number
      invoice_date?: string
    }) => {
      const response = await fetch('/api/supply-drafts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data || {}),
      })
      if (!response.ok) throw new Error('Failed to create supply draft')
      return response.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['supply-drafts'] })
    },
  })
}

// Pre-load all ingredients for instant client-side filtering (like Flask version)
export function useAllIngredients() {
  return useQuery<PosterItem[]>({
    queryKey: ['all-ingredients'],
    queryFn: async () => {
      // Fetch all items without query filter
      const response = await fetch('/api/items/search?q=&source=ingredient')
      if (!response.ok) throw new Error('Failed to fetch ingredients')
      return response.json()
    },
    staleTime: 5 * 60 * 1000, // 5 minutes cache - ingredients don't change often
  })
}

// Search ingredients for autocomplete (legacy - now using client-side filtering)
export function useSearchIngredients(query: string, enabled: boolean = true) {
  return useQuery<PosterItem[]>({
    queryKey: ['ingredients-search', query],
    queryFn: async () => {
      if (!query || query.length < 1) return []
      const response = await fetch(`/api/items/search?q=${encodeURIComponent(query)}&source=ingredient`)
      if (!response.ok) throw new Error('Failed to search ingredients')
      return response.json()
    },
    enabled: enabled && query.length >= 1,
    staleTime: 30000, // 30 seconds cache
  })
}

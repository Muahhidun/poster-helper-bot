import type {
  DashboardData,
  SuppliesResponse,
  SupplyDetail,
  AliasesResponse,
  CreateAliasRequest,
  UpdateAliasRequest,
  PosterItem,
  TemplatesResponse,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  SuppliersResponse,
  AccountsResponse,
  LastSupplyResponse,
  PriceHistoryResponse,
  CreateSupplyRequest,
  CreateSupplyResponse,
  PosterAccountsResponse,
} from '../types'

// Get API base URL from environment or use relative path
const API_BASE_URL = import.meta.env.VITE_API_URL || ''

interface ApiConfig {
  initData: string // Telegram WebApp initData for authentication
}

class ApiClient {
  private config: ApiConfig

  constructor(config: ApiConfig) {
    this.config = config
  }

  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`

    const headers = new Headers(options?.headers)
    headers.set('Content-Type', 'application/json')
    headers.set('X-Telegram-Init-Data', this.config.initData)

    const response = await fetch(url, {
      ...options,
      headers,
    })

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(`API Error: ${response.status} - ${errorText}`)
    }

    return response.json()
  }

  // Dashboard
  async getDashboard(): Promise<DashboardData> {
    return this.request<DashboardData>('/api/dashboard')
  }

  // Supply history
  async getSupplies(page = 1, limit = 20): Promise<SuppliesResponse> {
    return this.request<SuppliesResponse>(`/api/supplies?page=${page}&limit=${limit}`)
  }

  async getSupply(id: number): Promise<SupplyDetail> {
    return this.request<SupplyDetail>(`/api/supplies/${id}`)
  }

  // Aliases
  async getAliases(search?: string, source?: string): Promise<AliasesResponse> {
    const params = new URLSearchParams()
    if (search) params.set('search', search)
    if (source) params.set('source', source)

    const queryString = params.toString()
    const endpoint = queryString ? `/api/aliases?${queryString}` : '/api/aliases'

    return this.request<AliasesResponse>(endpoint)
  }

  async createAlias(data: CreateAliasRequest): Promise<{ id: number; success: boolean }> {
    return this.request('/api/aliases', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async updateAlias(
    id: number,
    data: UpdateAliasRequest
  ): Promise<{ success: boolean }> {
    return this.request(`/api/aliases/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  }

  async deleteAlias(id: number): Promise<{ success: boolean }> {
    return this.request(`/api/aliases/${id}`, {
      method: 'DELETE',
    })
  }

  // Search items (for autocomplete)
  async searchItems(query: string, source?: 'ingredient' | 'product'): Promise<PosterItem[]> {
    const params = new URLSearchParams()
    params.set('q', query)
    if (source) params.set('source', source)

    return this.request<PosterItem[]>(`/api/items/search?${params}`)
  }

  // Shipment Templates
  async getTemplates(): Promise<TemplatesResponse> {
    return this.request<TemplatesResponse>('/api/templates')
  }

  async createTemplate(data: CreateTemplateRequest): Promise<{ success: boolean }> {
    return this.request('/api/templates', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async updateTemplate(
    templateName: string,
    data: UpdateTemplateRequest
  ): Promise<{ success: boolean }> {
    return this.request(`/api/templates/${encodeURIComponent(templateName)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  }

  async deleteTemplate(templateName: string): Promise<{ success: boolean }> {
    return this.request(`/api/templates/${encodeURIComponent(templateName)}`, {
      method: 'DELETE',
    })
  }

  // Poster Business Accounts (multi-account support)
  async getPosterAccounts(): Promise<PosterAccountsResponse> {
    return this.request<PosterAccountsResponse>('/api/poster-accounts')
  }

  // Supply Creation
  async getSuppliers(): Promise<SuppliersResponse> {
    return this.request<SuppliersResponse>('/api/suppliers')
  }

  async getAccounts(): Promise<AccountsResponse> {
    return this.request<AccountsResponse>('/api/accounts')
  }

  async getLastSupply(supplierId: number): Promise<LastSupplyResponse> {
    return this.request<LastSupplyResponse>(`/api/supplies/last/${supplierId}`)
  }

  async getPriceHistory(itemId: number, supplierId?: number): Promise<PriceHistoryResponse> {
    const params = new URLSearchParams()
    if (supplierId) params.set('supplier_id', supplierId.toString())

    const queryString = params.toString()
    const endpoint = queryString
      ? `/api/items/price-history/${itemId}?${queryString}`
      : `/api/items/price-history/${itemId}`

    return this.request<PriceHistoryResponse>(endpoint)
  }

  async createSupply(data: CreateSupplyRequest): Promise<CreateSupplyResponse> {
    return this.request<CreateSupplyResponse>('/api/supplies/create', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }
}

// Singleton instance
let apiClient: ApiClient | null = null

export function initApiClient(initData: string): ApiClient {
  apiClient = new ApiClient({ initData })
  return apiClient
}

export function getApiClient(): ApiClient {
  if (!apiClient) {
    throw new Error('API client not initialized. Call initApiClient first.')
  }
  return apiClient
}

export default ApiClient

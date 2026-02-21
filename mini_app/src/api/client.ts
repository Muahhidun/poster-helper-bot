import type {
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
  ShiftClosingPosterData,
  ShiftClosingInput,
  ShiftClosingCalculateResponse,
  ExpensesResponse,
  UpdateExpenseRequest,
  CreateExpenseRequest,
  SyncFromPosterResponse,
  ShiftReconciliationResponse,
  SaveReconciliationRequest,
  ShiftClosingHistoryResponse,
  ShiftClosingDatesResponse,
  ShiftClosingReportResponse,
  ShiftClosingData,
  CashierEmployeesLastResponse,
  CashierSalaryCalculateRequest,
  CashierSalaryCalculateResponse,
  CashierSalaryCreateRequest,
  CashierSalaryCreateResponse,
  CashierShiftDataSaveRequest,
  CashierShiftDataStatusResponse,
  CafeEmployeesLastResponse,
  CafeSalaryCreateRequest,
  CafeSalaryCreateResponse,
  CafeSalaryStatusResponse,
  CafePosterData,
  CafeShiftInput,
  CafeCalculateResponse,
  CafeShiftData,
  CafeShiftHistoryResponse,
  CafeShiftDatesResponse,
  CafeReportResponse,
  CafeTransfersResponse,
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

  // Shift Closing (Закрытие смены)
  async getShiftClosingPosterData(date?: string): Promise<ShiftClosingPosterData> {
    const params = new URLSearchParams()
    if (date) params.set('date', date)

    const queryString = params.toString()
    const endpoint = queryString
      ? `/api/shift-closing/poster-data?${queryString}`
      : '/api/shift-closing/poster-data'

    return this.request<ShiftClosingPosterData>(endpoint)
  }

  async calculateShiftClosing(data: ShiftClosingInput): Promise<ShiftClosingCalculateResponse> {
    return this.request<ShiftClosingCalculateResponse>('/api/shift-closing/calculate', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async saveShiftClosing(data: ShiftClosingData & { date?: string }): Promise<{ success: boolean }> {
    return this.request('/api/shift-closing/save', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async getShiftClosingHistory(date: string): Promise<ShiftClosingHistoryResponse> {
    return this.request<ShiftClosingHistoryResponse>(`/api/shift-closing/history?date=${date}`)
  }

  async getShiftClosingDates(): Promise<ShiftClosingDatesResponse> {
    return this.request<ShiftClosingDatesResponse>('/api/shift-closing/dates')
  }

  async getShiftClosingReport(date?: string): Promise<ShiftClosingReportResponse> {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    const queryString = params.toString()
    const endpoint = queryString ? `/api/shift-closing/report?${queryString}` : '/api/shift-closing/report'
    return this.request<ShiftClosingReportResponse>(endpoint)
  }

  async createShiftClosingTransfers(date?: string): Promise<{ success: boolean; already_created?: boolean; created_count?: number; message?: string; transfers?: Array<{ name: string; amount: number; tx_id: number }> ; error?: string }> {
    return this.request('/api/shift-closing/transfers', {
      method: 'POST',
      body: JSON.stringify({ date }),
    })
  }

  // Expenses
  async getExpenses(date?: string): Promise<ExpensesResponse> {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    const queryString = params.toString()
    const endpoint = queryString ? `/api/expenses?${queryString}` : '/api/expenses'
    return this.request<ExpensesResponse>(endpoint)
  }

  async updateExpense(id: number, data: UpdateExpenseRequest): Promise<{ success: boolean }> {
    return this.request(`/api/expenses/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  }

  async createExpense(data: CreateExpenseRequest): Promise<{ id: number; success: boolean }> {
    return this.request('/api/expenses', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async deleteExpense(id: number): Promise<{ success: boolean }> {
    return this.request(`/api/expenses/${id}`, {
      method: 'DELETE',
    })
  }

  async syncExpensesFromPoster(): Promise<SyncFromPosterResponse> {
    return this.request<SyncFromPosterResponse>('/api/expenses/sync-from-poster', {
      method: 'POST',
    })
  }

  async processExpenseDrafts(draftIds: number[]): Promise<{ success: boolean }> {
    return this.request('/api/expenses/process', {
      method: 'POST',
      body: JSON.stringify({ draft_ids: draftIds }),
    })
  }

  async toggleExpenseType(id: number, expenseType: 'transaction' | 'supply'): Promise<{ success: boolean }> {
    return this.request(`/api/expenses/${id}/toggle-type`, {
      method: 'POST',
      body: JSON.stringify({ expense_type: expenseType }),
    })
  }

  async updateExpenseCompletionStatus(id: number, status: 'pending' | 'partial' | 'completed'): Promise<{ success: boolean }> {
    return this.request(`/api/expenses/${id}/completion-status`, {
      method: 'POST',
      body: JSON.stringify({ completion_status: status }),
    })
  }

  // Shift Reconciliation (сверка смены)
  async getShiftReconciliation(date?: string): Promise<ShiftReconciliationResponse> {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    const queryString = params.toString()
    const endpoint = queryString ? `/api/shift-reconciliation?${queryString}` : '/api/shift-reconciliation'
    return this.request<ShiftReconciliationResponse>(endpoint)
  }

  async saveShiftReconciliation(data: SaveReconciliationRequest): Promise<{ success: boolean }> {
    return this.request('/api/shift-reconciliation', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  // ==========================================
  // Cashier Shift Closing
  // ==========================================

  async getCashierEmployeesLast(): Promise<CashierEmployeesLastResponse> {
    return this.request<CashierEmployeesLastResponse>('/api/cashier/employees/last')
  }

  async calculateCashierSalaries(data: CashierSalaryCalculateRequest): Promise<CashierSalaryCalculateResponse> {
    return this.request<CashierSalaryCalculateResponse>('/api/cashier/salaries/calculate', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async createCashierSalaries(data: CashierSalaryCreateRequest): Promise<CashierSalaryCreateResponse> {
    return this.request<CashierSalaryCreateResponse>('/api/cashier/salaries/create', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async saveCashierShiftData(data: CashierShiftDataSaveRequest): Promise<{ success: boolean; date: string }> {
    return this.request('/api/cashier/shift-data/save', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async getCashierShiftDataStatus(): Promise<CashierShiftDataStatusResponse> {
    return this.request<CashierShiftDataStatusResponse>('/api/cashier/shift-data/status')
  }

  // ==========================================
  // Cafe Shift Closing
  // ==========================================

  async getCafeEmployeesLast(): Promise<CafeEmployeesLastResponse> {
    return this.request<CafeEmployeesLastResponse>('/api/cafe/employees/last')
  }

  async getCafeSalaryStatus(date?: string): Promise<CafeSalaryStatusResponse> {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    const qs = params.toString()
    return this.request<CafeSalaryStatusResponse>(qs ? `/api/cafe/salaries/status?${qs}` : '/api/cafe/salaries/status')
  }

  async createCafeSalaries(data: CafeSalaryCreateRequest): Promise<CafeSalaryCreateResponse> {
    return this.request<CafeSalaryCreateResponse>('/api/cafe/salaries/create', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async getCafePosterData(date?: string): Promise<CafePosterData> {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    const qs = params.toString()
    return this.request<CafePosterData>(qs ? `/api/cafe/poster-data?${qs}` : '/api/cafe/poster-data')
  }

  async calculateCafeShift(data: CafeShiftInput): Promise<CafeCalculateResponse> {
    return this.request<CafeCalculateResponse>('/api/cafe/calculate', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async saveCafeShift(data: CafeShiftData & { date?: string }): Promise<{ success: boolean }> {
    return this.request('/api/cafe/save', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async getCafeShiftHistory(date: string): Promise<CafeShiftHistoryResponse> {
    return this.request<CafeShiftHistoryResponse>(`/api/cafe/history?date=${date}`)
  }

  async getCafeShiftDates(): Promise<CafeShiftDatesResponse> {
    return this.request<CafeShiftDatesResponse>('/api/cafe/dates')
  }

  async getCafeReport(date?: string): Promise<CafeReportResponse> {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    const qs = params.toString()
    return this.request<CafeReportResponse>(qs ? `/api/cafe/report?${qs}` : '/api/cafe/report')
  }

  async createCafeTransfers(date?: string): Promise<CafeTransfersResponse> {
    return this.request<CafeTransfersResponse>('/api/cafe/transfers', {
      method: 'POST',
      body: JSON.stringify({ date }),
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

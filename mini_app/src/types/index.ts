// Telegram WebApp types
export interface TelegramUser {
  id: number
  first_name: string
  last_name?: string
  username?: string
  language_code?: string
}

export interface TelegramWebApp {
  initData: string
  initDataUnsafe: {
    user?: TelegramUser
    query_id?: string
    auth_date?: number
    hash?: string
  }
  version: string
  platform: string
  colorScheme: 'light' | 'dark'
  themeParams: {
    bg_color?: string
    text_color?: string
    hint_color?: string
    link_color?: string
    button_color?: string
    button_text_color?: string
    secondary_bg_color?: string
  }
  isExpanded: boolean
  viewportHeight: number
  viewportStableHeight: number
  headerColor: string
  backgroundColor: string
  MainButton: {
    text: string
    color: string
    textColor: string
    isVisible: boolean
    isActive: boolean
    isProgressVisible: boolean
    setText: (text: string) => void
    onClick: (callback: () => void) => void
    offClick: (callback: () => void) => void
    show: () => void
    hide: () => void
    enable: () => void
    disable: () => void
    showProgress: (leaveActive?: boolean) => void
    hideProgress: () => void
    setParams: (params: Partial<TelegramWebApp['MainButton']>) => void
  }
  BackButton: {
    isVisible: boolean
    onClick: (callback: () => void) => void
    offClick: (callback: () => void) => void
    show: () => void
    hide: () => void
  }
  HapticFeedback: {
    impactOccurred: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft') => void
    notificationOccurred: (type: 'error' | 'success' | 'warning') => void
    selectionChanged: () => void
  }
  ready: () => void
  expand: () => void
  close: () => void
  sendData: (data: string) => void
}

// Extend Window interface for Telegram
declare global {
  interface Window {
    Telegram?: {
      WebApp: TelegramWebApp
    }
  }
}

// API response types
export interface DashboardData {
  supplies_count: number
  items_count: number
  avg_accuracy: number
  accuracy_trend: Array<{
    date: string
    accuracy: number
  }>
  top_problematic: Array<{
    item: string
    count: number
  }>
}

export interface Supply {
  id: number
  created_at: string
  supplier_name: string
  account_name?: string
  storage_name?: string
  items_count: number
  total_amount: number
  avg_confidence: number
}

export interface SupplyItem {
  original_text: string
  matched_item_name: string
  quantity: number
  unit: string
  price: number
  total: number
  confidence_score: number
}

export interface SupplyDetail {
  id: number
  created_at: string
  supplier_name: string
  account_name: string
  storage_name: string
  total_amount: number
  poster_supply_id?: number
  items: SupplyItem[]
}

export interface SuppliesResponse {
  supplies: Supply[]
  total: number
  page: number
  pages: number
}

export interface Alias {
  id: number
  alias_text: string
  poster_item_id: number
  poster_item_name: string
  source: string
  notes?: string
}

export interface AliasesResponse {
  aliases: Alias[]
}

export interface CreateAliasRequest {
  alias_text: string
  poster_item_id: number
  poster_item_name: string
  source: string
  notes?: string
}

export interface UpdateAliasRequest {
  alias_text?: string
  poster_item_id?: number
  poster_item_name?: string
  source?: string
  notes?: string
}

export interface PosterItem {
  id: number
  name: string
  type: 'ingredient' | 'product'
  poster_account_id?: number    // Which Poster business account this item belongs to
  poster_account_name?: string  // Display name (e.g., "PizzBurg", "PizzBurg Cafe")
}

// Shipment Templates
export interface TemplateItem {
  id: number
  name: string
  price: number
}

export interface ShipmentTemplate {
  id?: number
  template_name: string
  supplier_id: number
  supplier_name: string
  account_id: number
  account_name: string
  storage_id: number
  items: TemplateItem[]
}

export interface TemplatesResponse {
  templates: ShipmentTemplate[]
}

export interface CreateTemplateRequest {
  template_name: string
  supplier_id: number
  supplier_name: string
  account_id: number
  account_name: string
  items: TemplateItem[]
  storage_id?: number
}

export interface UpdateTemplateRequest {
  supplier_id?: number
  supplier_name?: string
  account_id?: number
  account_name?: string
  items?: TemplateItem[]
  storage_id?: number
}

// Suppliers and Accounts
export interface Supplier {
  id: number
  name: string
  aliases: string[]
}

export interface SuppliersResponse {
  suppliers: Supplier[]
}

export interface Account {
  id: number
  name: string
  type: 'bank' | 'cash'
  aliases: string[]
}

export interface AccountsResponse {
  accounts: Account[]
}

// Poster Business Accounts (multi-account support)
export interface PosterAccount {
  id: number
  name: string
  base_url: string
  is_primary: boolean
}

export interface PosterAccountsResponse {
  poster_accounts: PosterAccount[]
}

// Supply Creation
export interface SupplyItemInput {
  id: number
  name: string
  type: 'ingredient' | 'product'
  quantity: number
  price: number
  unit: string
  sum?: number  // Optional: for UI calculations only (not sent to backend)
  poster_account_id?: number  // Which Poster account this item belongs to
}

export interface CreateSupplyRequest {
  supplier_id: number
  supplier_name: string
  account_id: number
  items: SupplyItemInput[]
  date?: string
  storage_id?: number
  poster_account_id?: number  // Which Poster business account (PizzBurg, PizzBurg Cafe, etc.)
}

export interface CreateSupplyResponse {
  success: boolean
  supply_id: number
}

// Last Supply
export interface LastSupplyItem {
  id: number
  name: string
  price: number
  quantity: number
  unit: string
  date: string
}

export interface LastSupplyResponse {
  supplier_id: number
  items: LastSupplyItem[]
}

// Price History
export interface PriceHistoryRecord {
  id: number
  ingredient_id: number
  ingredient_name: string
  supplier_id: number
  supplier_name: string
  date: string
  price: number
  quantity: number
  unit: string
  supply_id: number
  created_at: string
}

export interface PriceHistoryResponse {
  item_id: number
  history: PriceHistoryRecord[]
}

// Shift Closing (Закрытие смены)
export interface ShiftClosingPosterData {
  success: boolean
  date: string
  total_sum: number        // Общая сумма (с бонусами) - в тийинах
  trade_total: number      // Торговля за день (без бонусов) - в тийинах
  bonus: number            // Бонусы (онлайн-оплата) - в тийинах
  poster_cash: number      // Наличка в Poster - в тийинах
  poster_card: number      // Безнал в Poster (картой) - в тийинах
  shift_start: number      // Остаток на начало смены - в тийинах
  transactions_count: number
  error?: string
}

export interface ShiftClosingInput {
  wolt: number             // Wolt - в тенге
  halyk: number            // Halyk терминал - в тенге
  kaspi: number            // Kaspi терминал - в тенге
  kaspi_cafe: number       // Kaspi от PizzBurg-Cafe (вычитается) - в тенге
  cash_bills: number       // Наличка бумажная - в тенге
  cash_coins: number       // Наличка мелочь - в тенге
  shift_start: number      // Остаток на начало смены - в тенге
  deposits: number         // Внесения - в тенге
  expenses: number         // Расходы с кассы - в тенге
  cash_to_leave: number    // Оставить на смену (бумажные) - в тенге
  poster_trade: number     // Poster торговля - в тийинах
  poster_bonus: number     // Poster бонусы - в тийинах
  poster_card: number      // Poster картой - в тийинах
}

export interface ShiftClosingCalculations {
  // Input values echoed back
  wolt: number
  halyk: number
  kaspi: number
  kaspi_cafe: number
  cash_bills: number
  cash_coins: number
  shift_start: number
  deposits: number
  expenses: number
  cash_to_leave: number
  poster_trade: number
  poster_bonus: number
  poster_card: number

  // Calculated values (all in tenge)
  fact_cashless: number    // Итого безнал факт
  fact_total: number       // Фактический
  fact_adjusted: number    // Итого фактический
  poster_total: number     // Итого Poster
  day_result: number       // ИТОГО ДЕНЬ (излишек/недостача)
  shift_left: number       // Смена оставили
  collection: number       // Инкассация
  cashless_diff: number    // Разница безнала
}

export interface ShiftClosingCalculateResponse {
  success: boolean
  calculations: ShiftClosingCalculations
  error?: string
}

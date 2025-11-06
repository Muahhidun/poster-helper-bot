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

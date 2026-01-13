import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import type {
  Supplier,
  Account,
  SupplyItemInput,
  PosterItem,
  LastSupplyItem
} from '../types'

// Helper function to evaluate math expressions safely
function evaluateExpression(expr: string): number | null {
  try {
    // Remove spaces
    expr = expr.trim().replace(/\s/g, '')

    // Only allow numbers, decimal points, +, -, *, /
    if (!/^[\d+\-*/().]+$/.test(expr)) {
      return null
    }

    // Use Function constructor for safe eval (limited to math operations)
    const result = new Function(`return ${expr}`)()

    return typeof result === 'number' && !isNaN(result) ? result : null
  } catch {
    return null
  }
}

export const CreateSupply: React.FC = () => {
  const navigate = useNavigate()
  const { webApp, themeParams } = useTelegram()

  // Data loading
  const { data: suppliersData, loading: loadingSuppliers } = useApi(() =>
    getApiClient().getSuppliers()
  )
  const { data: accountsData, loading: loadingAccounts } = useApi(() =>
    getApiClient().getAccounts()
  )

  // Form state
  const [selectedSupplier, setSelectedSupplier] = useState<Supplier | null>(null)
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null)
  const [items, setItems] = useState<SupplyItemInput[]>([])
  const [supplierSearch, setSupplierSearch] = useState('')
  const [itemSearch, setItemSearch] = useState('')
  const [searchResults, setSearchResults] = useState<PosterItem[]>([])
  const [lastSupplyItems, setLastSupplyItems] = useState<LastSupplyItem[]>([])
  const [error, setError] = useState<string | null>(null)

  // Prioritize Kaspi Pay and Cash in accounts list
  const sortedAccounts = useMemo(() => {
    if (!accountsData?.accounts) return []

    const priority = ['Kaspi Pay', 'Денежный ящик']
    return [...accountsData.accounts].sort((a, b) => {
      const aIndex = priority.findIndex(p => a.name.includes(p))
      const bIndex = priority.findIndex(p => b.name.includes(p))

      if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex
      if (aIndex !== -1) return -1
      if (bIndex !== -1) return 1
      return a.name.localeCompare(b.name)
    })
  }, [accountsData])

  // Filter suppliers by search
  const filteredSuppliers = useMemo(() => {
    if (!suppliersData?.suppliers) return []
    if (!supplierSearch) return suppliersData.suppliers

    const search = supplierSearch.toLowerCase()
    return suppliersData.suppliers.filter(
      s => s.name.toLowerCase().includes(search) ||
           s.aliases.some(a => a.toLowerCase().includes(search))
    )
  }, [suppliersData, supplierSearch])

  // Load last supply when supplier changes
  useEffect(() => {
    if (selectedSupplier) {
      getApiClient()
        .getLastSupply(selectedSupplier.id)
        .then(response => setLastSupplyItems(response.items))
        .catch(() => setLastSupplyItems([]))
    }
  }, [selectedSupplier])

  // Search items with debounce
  useEffect(() => {
    if (!itemSearch || itemSearch.length < 2) {
      setSearchResults([])
      return
    }

    const timer = setTimeout(() => {
      getApiClient()
        .searchItems(itemSearch, 'ingredient')
        .then(results => {
          // Rank by: items from this supplier first
          const ranked = results.sort((a, b) => {
            const aInLast = lastSupplyItems.some(item => item.id === a.id)
            const bInLast = lastSupplyItems.some(item => item.id === b.id)

            if (aInLast && !bInLast) return -1
            if (!aInLast && bInLast) return 1
            return 0
          })

          setSearchResults(ranked.slice(0, 20))
        })
        .catch(() => setSearchResults([]))
    }, 300)

    return () => clearTimeout(timer)
  }, [itemSearch, lastSupplyItems])

  // Calculate total
  const total = useMemo(() => {
    return items.reduce((sum, item) => sum + item.quantity * item.price, 0)
  }, [items])

  // Handle item input changes with smart calculator
  const handleItemChange = (
    index: number,
    field: 'quantity' | 'price',
    value: string
  ) => {
    const newItems = [...items]
    const item = newItems[index]

    // Try to evaluate as expression
    const evaluated = evaluateExpression(value)

    if (evaluated !== null) {
      newItems[index] = { ...item, [field]: evaluated }
    } else {
      // Keep as number if possible
      const num = parseFloat(value)
      if (!isNaN(num)) {
        newItems[index] = { ...item, [field]: num }
      }
    }

    setItems(newItems)
  }

  // Add item from search
  const addItem = (posterItem: PosterItem) => {
    // Check if already exists
    if (items.some(i => i.id === posterItem.id)) {
      webApp?.HapticFeedback?.notificationOccurred('warning')
      return
    }

    // Find price from last supply
    const lastItem = lastSupplyItems.find(i => i.id === posterItem.id)

    const newItem: SupplyItemInput = {
      id: posterItem.id,
      name: posterItem.name,
      type: posterItem.type,
      quantity: lastItem?.quantity || 1,
      price: lastItem?.price || 0,
      unit: lastItem?.unit || 'шт',
    }

    setItems([...items, newItem])
    setItemSearch('')
    setSearchResults([])
    webApp?.HapticFeedback?.impactOccurred('light')
  }

  // Remove item
  const removeItem = (index: number) => {
    setItems(items.filter((_, i) => i !== index))
    webApp?.HapticFeedback?.impactOccurred('medium')
  }

  // Load last supply
  const loadLastSupply = () => {
    if (lastSupplyItems.length === 0) {
      webApp?.HapticFeedback?.notificationOccurred('error')
      return
    }

    const newItems: SupplyItemInput[] = lastSupplyItems.map(item => ({
      id: item.id,
      name: item.name,
      type: 'ingredient',
      quantity: item.quantity,
      price: item.price,
      unit: item.unit,
    }))

    setItems(newItems)
    webApp?.HapticFeedback?.notificationOccurred('success')
  }

  // Submit form
  const handleSubmit = async () => {
    if (!selectedSupplier || !selectedAccount || items.length === 0) {
      webApp?.HapticFeedback?.notificationOccurred('error')
      setError('Заполните все обязательные поля')
      return
    }

    // Validate all items have quantity and price
    if (items.some(item => item.quantity <= 0 || item.price <= 0)) {
      webApp?.HapticFeedback?.notificationOccurred('error')
      setError('Все товары должны иметь количество и цену больше 0')
      return
    }

    setIsSubmitting(true)
    setError(null)
    webApp?.MainButton?.showProgress()

    try {
      await getApiClient().createSupply({
        supplier_id: selectedSupplier.id,
        supplier_name: selectedSupplier.name,
        account_id: selectedAccount.id,
        items: items,
      })

      webApp?.HapticFeedback?.notificationOccurred('success')
      webApp?.MainButton?.hideProgress()
      navigate('/supplies')
    } catch (err) {
      webApp?.HapticFeedback?.notificationOccurred('error')
      setError(err instanceof Error ? err.message : 'Ошибка создания поставки')
      webApp?.MainButton?.hideProgress()
      setIsSubmitting(false)
    }
  }

  // Setup main button
  useEffect(() => {
    if (!webApp?.MainButton) return

    const canSubmit = selectedSupplier && selectedAccount && items.length > 0

    if (canSubmit) {
      webApp?.MainButton.setText('Создать поставку')
      webApp?.MainButton.show()
      webApp?.MainButton.enable()
      webApp?.MainButton.onClick(handleSubmit)
    } else {
      webApp?.MainButton.hide()
    }

    return () => {
      webApp?.MainButton.offClick(handleSubmit)
      webApp?.MainButton.hide()
    }
  }, [webApp?.MainButton, selectedSupplier, selectedAccount, items])

  // Setup back button
  useEffect(() => {
    if (!webApp?.BackButton) return

    webApp?.BackButton.show()
    webApp?.BackButton.onClick(() => navigate('/'))

    return () => {
      webApp?.BackButton.offClick(() => navigate('/'))
      webApp?.BackButton.hide()
    }
  }, [webApp?.BackButton, navigate])

  if (loadingSuppliers || loadingAccounts) return <Loading />

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen pb-24">
      <Header title="Новая поставка" />

      <div className="p-4">
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-100 text-red-800">
            {error}
          </div>
        )}

        {/* Supplier Selection */}
        <div className="mb-6">
          <label
            className="block text-sm font-medium mb-2"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            Поставщик *
          </label>

          <input
            type="text"
            placeholder="Поиск поставщика..."
            value={supplierSearch}
            onChange={(e) => setSupplierSearch(e.target.value)}
            className="w-full p-4 rounded-lg border text-lg"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
              color: themeParams.text_color || '#000000',
              borderColor: themeParams.hint_color || '#d1d5db',
            }}
          />

          {supplierSearch && (
            <div className="mt-2 max-h-48 overflow-y-auto space-y-2">
              {filteredSuppliers.map(supplier => (
                <button
                  key={supplier.id}
                  onClick={() => {
                    setSelectedSupplier(supplier)
                    setSupplierSearch('')
                    webApp?.HapticFeedback?.selectionChanged()
                  }}
                  className="w-full p-3 rounded-lg text-left"
                  style={{
                    backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
                    color: themeParams.text_color || '#000000',
                  }}
                >
                  {supplier.name}
                </button>
              ))}
            </div>
          )}

          {selectedSupplier && (
            <div
              className="mt-2 p-3 rounded-lg flex items-center justify-between"
              style={{
                backgroundColor: themeParams.button_color || '#3b82f6',
                color: themeParams.button_text_color || '#ffffff',
              }}
            >
              <span className="font-medium">{selectedSupplier.name}</span>
              <button
                onClick={() => {
                  setSelectedSupplier(null)
                  setLastSupplyItems([])
                  webApp?.HapticFeedback?.impactOccurred('light')
                }}
                className="text-xl"
              >
                ✕
              </button>
            </div>
          )}
        </div>

        {/* Account Selection */}
        <div className="mb-6">
          <label
            className="block text-sm font-medium mb-2"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            Счет *
          </label>

          <div className="grid grid-cols-2 gap-2">
            {sortedAccounts.map(account => (
              <button
                key={account.id}
                onClick={() => {
                  setSelectedAccount(account)
                  webApp?.HapticFeedback?.selectionChanged()
                }}
                className="p-4 rounded-lg font-medium text-center"
                style={{
                  backgroundColor:
                    selectedAccount?.id === account.id
                      ? themeParams.button_color || '#3b82f6'
                      : themeParams.secondary_bg_color || '#f3f4f6',
                  color:
                    selectedAccount?.id === account.id
                      ? themeParams.button_text_color || '#ffffff'
                      : themeParams.text_color || '#000000',
                }}
              >
                {account.name}
              </button>
            ))}
          </div>
        </div>

        {/* Load Last Supply Button */}
        {selectedSupplier && lastSupplyItems.length > 0 && items.length === 0 && (
          <button
            onClick={loadLastSupply}
            className="w-full mb-4 p-4 rounded-lg font-medium"
            style={{
              backgroundColor: themeParams.button_color || '#3b82f6',
              color: themeParams.button_text_color || '#ffffff',
            }}
          >
            🔄 Повторить последнюю поставку ({lastSupplyItems.length} товаров)
          </button>
        )}

        {/* Items List */}
        <div className="mb-6">
          <label
            className="block text-sm font-medium mb-2"
            style={{ color: themeParams.text_color || '#000000' }}
          >
            Товары *
          </label>

          {/* Item Search */}
          <input
            type="text"
            placeholder="Добавить товар..."
            value={itemSearch}
            onChange={(e) => setItemSearch(e.target.value)}
            className="w-full p-4 rounded-lg border text-lg mb-2"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
              color: themeParams.text_color || '#000000',
              borderColor: themeParams.hint_color || '#d1d5db',
            }}
          />

          {/* Search Results */}
          {searchResults.length > 0 && (
            <div className="mb-4 max-h-48 overflow-y-auto space-y-2">
              {searchResults.map(item => {
                const inLast = lastSupplyItems.some(i => i.id === item.id)
                return (
                  <button
                    key={item.id}
                    onClick={() => addItem(item)}
                    className="w-full p-3 rounded-lg text-left flex items-center justify-between"
                    style={{
                      backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
                      color: themeParams.text_color || '#000000',
                    }}
                  >
                    <span>{item.name}</span>
                    {inLast && <span className="text-xs">⭐ Недавно</span>}
                  </button>
                )
              })}
            </div>
          )}

          {/* Items List */}
          <div className="space-y-3">
            {items.map((item, index) => (
              <div
                key={index}
                className="p-4 rounded-lg"
                style={{
                  backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
                }}
              >
                <div className="flex items-start justify-between mb-3">
                  <span
                    className="font-medium flex-1"
                    style={{ color: themeParams.text_color || '#000000' }}
                  >
                    {item.name}
                  </span>
                  <button
                    onClick={() => removeItem(index)}
                    className="text-red-600 text-xl ml-2"
                  >
                    ✕
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-xs mb-1" style={{ color: themeParams.hint_color }}>
                      Количество
                    </label>
                    <input
                      type="text"
                      value={item.quantity}
                      onChange={(e) => handleItemChange(index, 'quantity', e.target.value)}
                      className="w-full p-3 rounded-lg border text-lg"
                      style={{
                        backgroundColor: themeParams.bg_color || '#ffffff',
                        color: themeParams.text_color || '#000000',
                        borderColor: themeParams.hint_color || '#d1d5db',
                      }}
                    />
                  </div>

                  <div>
                    <label className="block text-xs mb-1" style={{ color: themeParams.hint_color }}>
                      Цена
                    </label>
                    <input
                      type="text"
                      value={item.price}
                      onChange={(e) => handleItemChange(index, 'price', e.target.value)}
                      className="w-full p-3 rounded-lg border text-lg"
                      style={{
                        backgroundColor: themeParams.bg_color || '#ffffff',
                        color: themeParams.text_color || '#000000',
                        borderColor: themeParams.hint_color || '#d1d5db',
                      }}
                    />
                  </div>
                </div>

                <div className="mt-2 text-right">
                  <span
                    className="text-sm font-medium"
                    style={{ color: themeParams.hint_color }}
                  >
                    Сумма: {(item.quantity * item.price).toLocaleString('ru-RU')} ₸
                  </span>
                </div>
              </div>
            ))}
          </div>

          {items.length === 0 && (
            <div
              className="text-center py-8"
              style={{ color: themeParams.hint_color }}
            >
              Добавьте товары через поиск выше
            </div>
          )}
        </div>

        {/* Total */}
        {items.length > 0 && (
          <div
            className="p-4 rounded-lg"
            style={{
              backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
            }}
          >
            <div className="flex items-center justify-between">
              <span
                className="text-lg font-semibold"
                style={{ color: themeParams.text_color || '#000000' }}
              >
                Итого:
              </span>
              <span
                className="text-2xl font-bold"
                style={{ color: themeParams.button_color || '#3b82f6' }}
              >
                {total.toLocaleString('ru-RU')} ₸
              </span>
            </div>
            <div className="text-sm mt-1" style={{ color: themeParams.hint_color }}>
              {items.length} {items.length === 1 ? 'товар' : 'товаров'}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

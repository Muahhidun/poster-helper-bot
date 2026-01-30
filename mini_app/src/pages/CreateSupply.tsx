import React, { useState, useEffect, useMemo, useRef } from 'react'
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
    // Remove spaces and replace comma with dot for decimal separator
    expr = expr.trim().replace(/\s/g, '').replace(/,/g, '.')

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
  const [isSubmitting, setIsSubmitting] = useState(false)
  // Track raw input strings for quantity, price and sum to allow partial expressions
  const [inputValues, setInputValues] = useState<Record<number, { quantity?: string; price?: string; sum?: string }>>({})
  // Track last edited field for each item to determine what to recalculate when sum changes
  const [lastEditedField, setLastEditedField] = useState<Record<number, 'quantity' | 'price' | 'sum'>>({})
  // Track focused field for showing operator panel
  const [focusedField, setFocusedField] = useState<{ index: number; field: 'quantity' | 'price' | 'sum' } | null>(null)

  // Refs for input fields (for Enter key navigation)
  const supplierSearchRef = useRef<HTMLInputElement>(null)
  const itemSearchRef = useRef<HTMLInputElement>(null)
  const itemInputRefs = useRef<Record<number, {
    quantity: HTMLInputElement | null
    price: HTMLInputElement | null
    sum: HTMLInputElement | null
  }>>({})

  // Ref to track if sum field should be skipped after price
  const skipSumFieldRef = useRef<boolean>(false)

  // Filter to show only Kaspi Pay and "Оставил в кассе (на закупы)"
  const sortedAccounts = useMemo(() => {
    if (!accountsData?.accounts) return []

    const allowedAccounts = ['Kaspi Pay', 'Оставил в кассе']
    return accountsData.accounts.filter(account =>
      allowedAccounts.some(allowed => account.name.includes(allowed))
    )
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

  // Hide operator panel when clicking outside input fields
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement
      // Check if click is outside input fields and operator panel
      if (!target.closest('input') && !target.closest('button[type="button"]')) {
        setFocusedField(null)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

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

  // Handle item input changes - store raw string to allow partial expressions
  const handleItemChange = (
    index: number,
    field: 'quantity' | 'price' | 'sum',
    value: string
  ) => {
    // Store raw input string
    setInputValues(prev => ({
      ...prev,
      [index]: { ...prev[index], [field]: value }
    }))
  }

  // Handle focus - clear field if it has default value
  const handleItemFocus = (
    index: number,
    field: 'quantity' | 'price' | 'sum'
  ) => {
    // Set focused field for operator panel
    setFocusedField({ index, field })

    const item = items[index]
    const currentValue = inputValues[index]?.[field]

    // If there's already a custom input value, don't clear it
    if (currentValue !== undefined) return

    // Clear field if it has default value
    const shouldClear =
      (field === 'quantity' && item.quantity === 1) ||
      (field === 'price' && item.price === 0) ||
      (field === 'sum' && (item.sum === 0 || item.sum === item.quantity * item.price))

    if (shouldClear) {
      setInputValues(prev => ({
        ...prev,
        [index]: { ...prev[index], [field]: '' }
      }))
    }
  }

  // Insert operator into focused field
  const insertOperator = (operator: string) => {
    if (!focusedField) return

    const { index, field } = focusedField
    const item = items[index]
    const currentValue = inputValues[index]?.[field]

    // Get current value as string
    let valueStr = currentValue !== undefined ? currentValue : String(item[field])

    // Append operator to the end
    valueStr += operator

    // Update input value
    setInputValues(prev => ({
      ...prev,
      [index]: { ...prev[index], [field]: valueStr }
    }))

    // Refocus the input field
    setTimeout(() => {
      itemInputRefs.current[index]?.[field]?.focus()
    }, 0)
  }

  // Handle blur - evaluate expression and update item with smart recalculation
  const handleItemBlur = (
    index: number,
    field: 'quantity' | 'price' | 'sum'
  ) => {
    const rawValue = inputValues[index]?.[field]

    // If field is empty, restore default value
    if (rawValue === undefined || rawValue === '') {
      const newItems = [...items]
      const item = newItems[index]

      // Restore default values
      if (field === 'quantity' && item.quantity === 0) {
        newItems[index].quantity = 1
        newItems[index].sum = 1 * item.price
      } else if (field === 'price' && item.price === 0) {
        // Keep price as 0 if not entered
        newItems[index].sum = item.quantity * 0
      } else if (field === 'sum' && item.sum === 0) {
        // Recalculate sum from quantity and price
        newItems[index].sum = item.quantity * item.price
      }

      setItems(newItems)

      // Clear the empty input value
      setInputValues(prev => {
        const updated = { ...prev }
        if (updated[index]) {
          delete updated[index][field]
        }
        return updated
      })
      return
    }

    const newItems = [...items]
    const item = newItems[index]

    // Try to evaluate as expression
    const evaluated = evaluateExpression(rawValue)

    if (evaluated !== null && evaluated > 0) {
      newItems[index] = { ...item, [field]: evaluated }

      // Smart recalculation based on which field was changed
      if (field === 'quantity') {
        // quantity changed → recalculate sum = quantity * price
        newItems[index].sum = evaluated * item.price
        skipSumFieldRef.current = false // Reset flag
      } else if (field === 'price') {
        // price changed → recalculate sum = quantity * price
        newItems[index].sum = item.quantity * evaluated
        // Set flag to skip sum field if price is valid
        skipSumFieldRef.current = (evaluated > 0 && item.quantity > 0)
      } else if (field === 'sum') {
        skipSumFieldRef.current = false // Reset flag
        // sum changed → recalculate price or quantity based on previous edit
        const previousField = lastEditedField[index]

        if (previousField === 'quantity' && item.quantity > 0) {
          // User edited quantity, then sum → recalculate price
          newItems[index].price = evaluated / item.quantity
        } else if (previousField === 'price' && item.price > 0) {
          // User edited price, then sum → recalculate quantity
          newItems[index].quantity = evaluated / item.price
        } else {
          // Default: recalculate price if quantity > 0
          if (item.quantity > 0) {
            newItems[index].price = evaluated / item.quantity
          }
        }
      }
    } else {
      // Try as number
      const num = parseFloat(rawValue)
      if (!isNaN(num) && num > 0) {
        newItems[index] = { ...item, [field]: num }

        // Same recalculation logic
        if (field === 'quantity') {
          newItems[index].sum = num * item.price
          skipSumFieldRef.current = false // Reset flag
        } else if (field === 'price') {
          newItems[index].sum = item.quantity * num
          // Set flag to skip sum field if price is valid
          skipSumFieldRef.current = (num > 0 && item.quantity > 0)
        } else if (field === 'sum') {
          skipSumFieldRef.current = false // Reset flag
          const previousField = lastEditedField[index]

          if (previousField === 'quantity' && item.quantity > 0) {
            newItems[index].price = num / item.quantity
          } else if (previousField === 'price' && item.price > 0) {
            newItems[index].quantity = num / item.price
          } else {
            if (item.quantity > 0) {
              newItems[index].price = num / item.quantity
            }
          }
        }
      } else {
        // Invalid input - reset to current value
        newItems[index] = { ...item, [field]: item[field] }
      }
    }

    setItems(newItems)

    // Remember what field was edited (for future sum calculations)
    if (field !== 'sum') {
      setLastEditedField(prev => ({ ...prev, [index]: field }))
    }

    // Clear raw input so it shows the evaluated number
    setInputValues(prev => {
      const updated = { ...prev }
      if (updated[index]) {
        delete updated[index][field]
      }
      return updated
    })

    // Don't clear focusedField here - it will be updated by next focus
    // This fixes the bug where panel doesn't show when switching between fields quickly
  }

  // Add item from search
  const addItem = (posterItem: PosterItem) => {
    // Find price from last supply
    const lastItem = lastSupplyItems.find(i => i.id === posterItem.id)

    const quantity = lastItem?.quantity || 1
    const price = lastItem?.price || 0

    const newItem: SupplyItemInput = {
      id: posterItem.id,
      name: posterItem.name,
      type: posterItem.type,
      quantity: quantity,
      price: price,
      unit: lastItem?.unit || 'шт',
      sum: quantity * price,  // Initialize sum
      poster_account_id: posterItem.poster_account_id,  // Track which Poster account this item belongs to
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

  // Handle Enter key in supplier search
  const handleSupplierSearchEnter = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (filteredSuppliers.length > 0) {
        setSelectedSupplier(filteredSuppliers[0])
        setSupplierSearch('')
        webApp?.HapticFeedback?.selectionChanged()
        // Focus on item search field
        setTimeout(() => itemSearchRef.current?.focus(), 0)
      }
    }
  }

  // Handle Enter key in item search
  const handleItemSearchEnter = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (searchResults.length > 0) {
        const item = searchResults[0]
        addItem(item)
        // Focus on quantity field of the newly added item
        const newIndex = items.length
        setTimeout(() => {
          if (itemInputRefs.current[newIndex]?.quantity) {
            itemInputRefs.current[newIndex].quantity?.focus()
          }
        }, 0)
      }
    }
  }

  // Handle Enter key in item fields (quantity, price, sum)
  const handleItemFieldEnter = (
    e: React.KeyboardEvent<HTMLInputElement>,
    index: number,
    field: 'quantity' | 'price' | 'sum'
  ) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleDoneButton(index, field)
    }
  }

  // Handle "Done" button click - same logic as Enter key
  const handleDoneButton = (index: number, field: 'quantity' | 'price' | 'sum') => {
    // Trigger blur to save the value
    handleItemBlur(index, field)

    // Determine next field
    setTimeout(() => {
      if (field === 'quantity') {
        // quantity → price
        itemInputRefs.current[index]?.price?.focus()
      } else if (field === 'price') {
        // Smart navigation: check if sum should be skipped
        if (skipSumFieldRef.current) {
          // Sum is automatically calculated → skip to next item
          itemSearchRef.current?.focus()
          itemSearchRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        } else {
          // Sum was manually edited or needs attention → go to sum field
          itemInputRefs.current[index]?.sum?.focus()
        }
      } else if (field === 'sum') {
        // sum → item search (for next item)
        itemSearchRef.current?.focus()
        // Scroll to item search
        itemSearchRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }, 0)
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
      sum: item.quantity * item.price,  // Initialize sum
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

    setError(null)
    setIsSubmitting(true)
    webApp?.MainButton?.showProgress()

    try {
      // Items contain poster_account_id - backend will group and create multiple supplies if needed
      await getApiClient().createSupply({
        supplier_id: selectedSupplier.id,
        supplier_name: selectedSupplier.name,
        account_id: selectedAccount.id,
        items: items,
      })

      webApp?.HapticFeedback?.notificationOccurred('success')
      webApp?.MainButton?.hideProgress()
      setIsSubmitting(false)
      navigate('/supplies')
    } catch (err) {
      webApp?.HapticFeedback?.notificationOccurred('error')
      setError(err instanceof Error ? err.message : 'Ошибка создания поставки')
      webApp?.MainButton?.hideProgress()
      setIsSubmitting(false)
    }
  }

  // Check if we're actually in Telegram (has real init data, not just the SDK loaded)
  const isInTelegram = !!(webApp?.initData && webApp.initData.length > 0)

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
      <Header title="Новая поставка" showBack={!isInTelegram} />

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
            ref={supplierSearchRef}
            type="text"
            placeholder="Поиск поставщика..."
            value={supplierSearch}
            onChange={(e) => setSupplierSearch(e.target.value)}
            onKeyDown={handleSupplierSearchEnter}
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
            ref={itemSearchRef}
            type="text"
            placeholder="Добавить товар..."
            value={itemSearch}
            onChange={(e) => setItemSearch(e.target.value)}
            onKeyDown={handleItemSearchEnter}
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
                    key={`${item.id}-${item.poster_account_id}`}
                    onClick={() => addItem(item)}
                    className="w-full p-3 rounded-lg text-left flex items-center justify-between"
                    style={{
                      backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
                      color: themeParams.text_color || '#000000',
                    }}
                  >
                    <div className="flex flex-col">
                      <span>{item.name}</span>
                      {item.poster_account_name && (
                        <span className="text-xs" style={{ color: themeParams.hint_color }}>
                          {item.poster_account_name}
                        </span>
                      )}
                    </div>
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

                <div className="space-y-2">
                  {/* Operator Panel - shows when field is focused */}
                  {focusedField && focusedField.index === index && (
                    <div
                      className="flex gap-2 p-2 rounded-lg"
                      style={{
                        backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
                      }}
                    >
                      <button
                        type="button"
                        onMouseDown={(e) => {
                          e.preventDefault()
                          insertOperator('+')
                        }}
                        className="flex-1 py-2 px-4 rounded-lg font-bold text-xl"
                        style={{
                          backgroundColor: themeParams.button_color || '#007AFF',
                          color: themeParams.button_text_color || '#ffffff',
                        }}
                      >
                        +
                      </button>
                      <button
                        type="button"
                        onMouseDown={(e) => {
                          e.preventDefault()
                          insertOperator('-')
                        }}
                        className="flex-1 py-2 px-4 rounded-lg font-bold text-xl"
                        style={{
                          backgroundColor: themeParams.button_color || '#007AFF',
                          color: themeParams.button_text_color || '#ffffff',
                        }}
                      >
                        −
                      </button>
                      <button
                        type="button"
                        onMouseDown={(e) => {
                          e.preventDefault()
                          insertOperator('*')
                        }}
                        className="flex-1 py-2 px-4 rounded-lg font-bold text-xl"
                        style={{
                          backgroundColor: themeParams.button_color || '#007AFF',
                          color: themeParams.button_text_color || '#ffffff',
                        }}
                      >
                        ×
                      </button>
                      <button
                        type="button"
                        onMouseDown={(e) => {
                          e.preventDefault()
                          insertOperator('/')
                        }}
                        className="flex-1 py-2 px-4 rounded-lg font-bold text-xl"
                        style={{
                          backgroundColor: themeParams.button_color || '#007AFF',
                          color: themeParams.button_text_color || '#ffffff',
                        }}
                      >
                        ÷
                      </button>
                      <button
                        type="button"
                        onMouseDown={(e) => {
                          e.preventDefault()
                          handleDoneButton(index, focusedField.field)
                        }}
                        className="flex-1 py-2 px-4 rounded-lg font-bold text-2xl"
                        style={{
                          backgroundColor: '#34C759',
                          color: '#ffffff',
                        }}
                      >
                        ✓
                      </button>
                    </div>
                  )}

                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <label className="block text-xs mb-1" style={{ color: themeParams.hint_color }}>
                        Кол-во
                      </label>
                      <input
                        ref={(el) => {
                          if (!itemInputRefs.current[index]) itemInputRefs.current[index] = { quantity: null, price: null, sum: null }
                          itemInputRefs.current[index].quantity = el
                        }}
                        type="text"
                        inputMode="decimal"
                        value={inputValues[index]?.quantity ?? item.quantity}
                        onChange={(e) => handleItemChange(index, 'quantity', e.target.value)}
                        onFocus={() => handleItemFocus(index, 'quantity')}
                        onBlur={() => handleItemBlur(index, 'quantity')}
                        onKeyDown={(e) => handleItemFieldEnter(e, index, 'quantity')}
                        className="w-full p-2 rounded-lg border text-base"
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
                        ref={(el) => {
                          if (!itemInputRefs.current[index]) itemInputRefs.current[index] = { quantity: null, price: null, sum: null }
                          itemInputRefs.current[index].price = el
                        }}
                        type="text"
                        inputMode="decimal"
                        value={inputValues[index]?.price ?? item.price}
                        onChange={(e) => handleItemChange(index, 'price', e.target.value)}
                        onFocus={() => handleItemFocus(index, 'price')}
                        onBlur={() => handleItemBlur(index, 'price')}
                        onKeyDown={(e) => handleItemFieldEnter(e, index, 'price')}
                        className="w-full p-2 rounded-lg border text-base"
                        style={{
                          backgroundColor: themeParams.bg_color || '#ffffff',
                          color: themeParams.text_color || '#000000',
                          borderColor: themeParams.hint_color || '#d1d5db',
                        }}
                      />
                    </div>

                    <div>
                      <label className="block text-xs mb-1" style={{ color: themeParams.hint_color }}>
                        Сумма
                      </label>
                      <input
                        ref={(el) => {
                          if (!itemInputRefs.current[index]) itemInputRefs.current[index] = { quantity: null, price: null, sum: null }
                          itemInputRefs.current[index].sum = el
                        }}
                        type="text"
                        inputMode="decimal"
                        value={inputValues[index]?.sum ?? (item.sum || item.quantity * item.price)}
                        onChange={(e) => handleItemChange(index, 'sum', e.target.value)}
                        onFocus={() => handleItemFocus(index, 'sum')}
                        onBlur={() => handleItemBlur(index, 'sum')}
                        onKeyDown={(e) => handleItemFieldEnter(e, index, 'sum')}
                        className="w-full p-2 rounded-lg border text-base font-medium"
                        style={{
                          backgroundColor: themeParams.bg_color || '#ffffff',
                          color: themeParams.text_color || '#000000',
                          borderColor: themeParams.hint_color || '#d1d5db',
                        }}
                      />
                    </div>
                  </div>
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

        {/* Fallback submit button for desktop (when not in Telegram) */}
        {!isInTelegram && (
          <button
            onClick={handleSubmit}
            disabled={!selectedSupplier || !selectedAccount || items.length === 0 || isSubmitting}
            className="w-full mt-6 p-4 rounded-lg font-semibold text-lg transition-opacity"
            style={{
              backgroundColor: (selectedSupplier && selectedAccount && items.length > 0)
                ? (themeParams.button_color || '#3b82f6')
                : '#9ca3af',
              color: themeParams.button_text_color || '#ffffff',
              opacity: isSubmitting ? 0.7 : 1,
              cursor: (selectedSupplier && selectedAccount && items.length > 0 && !isSubmitting)
                ? 'pointer'
                : 'not-allowed',
            }}
          >
            {isSubmitting ? 'Создание...' : 'Создать поставку'}
          </button>
        )}
      </div>
    </div>
  )
}

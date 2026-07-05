import { useState, useEffect } from 'react'
import { getApiClient } from '@/api/client'
import { 
  PurchaseSupplier,
  PurchaseIngredient,
  BlankResponse
} from '@/types'
import { 
  ShoppingCart, 
  Calendar, 
  Check, 
  AlertCircle,
  Plus,
  Minus,
  RefreshCw,
  Eye,
  EyeOff
} from 'lucide-react'

export function Purchase() {
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState<number | null>(null) // supplier_id being submitted
  const [date, setDate] = useState<string>(() => {
    const today = new Date()
    return today.toISOString().split('T')[0]
  })
  
  const [data, setData] = useState<BlankResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  
  // Filters
  const [onlyToday, setOnlyToday] = useState(true)
  const [activeSupplierId, setActiveSupplierId] = useState<number | null>(null)
  
  const fetchBlank = async (targetDate: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await getApiClient().getPurchaseBlank(targetDate)
      
      // Initialize items inputs
      const updatedSuppliers = response.suppliers.map((sup: PurchaseSupplier) => ({
        ...sup,
        ingredients: sup.ingredients.map((ing: PurchaseIngredient) => ({
          ...ing,
          actual_stock: '',
          order_qty: Math.max(0, ing.target_stock)
        }))
      }))
      
      setData({
        ...response,
        suppliers: updatedSuppliers
      })
      
      // Auto-set first active supplier
      const todaySuppliers = updatedSuppliers.filter((s: PurchaseSupplier) => !onlyToday || s.is_order_day)
      if (todaySuppliers.length > 0) {
        setActiveSupplierId(todaySuppliers[0].id)
      } else if (updatedSuppliers.length > 0) {
        setActiveSupplierId(updatedSuppliers[0].id)
      }
    } catch (err: any) {
      console.error(err)
      setError(err?.message || 'Не удалось загрузить бланк закупа')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchBlank(date)
  }, [date])

  const handleActualStockChange = (supplierId: number, ingId: number, val: string) => {
    if (!data) return
    
    // Allow digits and single decimal dot
    const cleanVal = val.replace(/[^0-9.]/g, '')
    
    setData(prev => {
      if (!prev) return null
      return {
        ...prev,
        suppliers: prev.suppliers.map((sup: PurchaseSupplier) => {
          if (sup.id !== supplierId) return sup
          return {
            ...sup,
            ingredients: sup.ingredients.map((ing: PurchaseIngredient) => {
              if (ing.id !== ingId) return ing
              
              const actual = cleanVal === '' ? 0 : parseFloat(cleanVal)
              const orderQty = Math.max(0, Number((ing.target_stock - actual).toFixed(2)))
              
              return {
                ...ing,
                actual_stock: cleanVal,
                order_qty: orderQty
              }
            })
          }
        })
      }
    })
  }

  const handleTargetStockChange = (supplierId: number, ingId: number, val: string) => {
    if (!data) return
    
    const cleanVal = val.replace(/[^0-9.]/g, '')
    
    setData(prev => {
      if (!prev) return null
      return {
        ...prev,
        suppliers: prev.suppliers.map((sup: PurchaseSupplier) => {
          if (sup.id !== supplierId) return sup
          return {
            ...sup,
            ingredients: sup.ingredients.map((ing: PurchaseIngredient) => {
              if (ing.id !== ingId) return ing
              
              const target = cleanVal === '' ? 0 : parseFloat(cleanVal)
              const actual = (ing.actual_stock || '') === '' ? 0 : parseFloat(ing.actual_stock || '')
              const orderQty = Math.max(0, Number((target - actual).toFixed(2)))
              
              return {
                ...ing,
                target_stock: target,
                order_qty: orderQty
              }
            })
          }
        })
      }
    })
  }

  const adjustStock = (supplierId: number, ingId: number, field: 'actual' | 'target', amount: number) => {
    if (!data) return
    
    setData(prev => {
      if (!prev) return null
      return {
        ...prev,
        suppliers: prev.suppliers.map((sup: PurchaseSupplier) => {
          if (sup.id !== supplierId) return sup
          return {
            ...sup,
            ingredients: sup.ingredients.map((ing: PurchaseIngredient) => {
              if (ing.id !== ingId) return ing
              
              if (field === 'actual') {
                const currentActual = (ing.actual_stock || '') === '' ? 0 : parseFloat(ing.actual_stock || '')
                const newActual = Math.max(0, currentActual + amount)
                const orderQty = Math.max(0, Number((ing.target_stock - newActual).toFixed(2)))
                return {
                  ...ing,
                  actual_stock: String(newActual),
                  order_qty: orderQty
                }
              } else {
                const newTarget = Math.max(0, ing.target_stock + amount)
                const actual = (ing.actual_stock || '') === '' ? 0 : parseFloat(ing.actual_stock || '')
                const orderQty = Math.max(0, Number((newTarget - actual).toFixed(2)))
                return {
                  ...ing,
                  target_stock: newTarget,
                  order_qty: orderQty
                }
              }
            })
          }
        })
      }
    })
  }

  const handleSubmit = async (supplier: PurchaseSupplier) => {
    setSubmitting(supplier.id)
    try {
      const items = supplier.ingredients.map(ing => ({
        name: ing.name,
        target_stock: ing.target_stock,
        actual_stock: (ing.actual_stock || '') === '' ? 0 : parseFloat(ing.actual_stock || ''),
        order_qty: ing.order_qty || 0
      }))
      
      await getApiClient().submitPurchase({
        date,
        supplier_id: supplier.id,
        items
      })
      
      alert(`Заказ для ${supplier.name} успешно отправлен в бот! Вы получите готовые сообщения в Telegram для пересылки поставщикам в WhatsApp.`)
    } catch (err: any) {
      console.error(err)
      const errorMsg = err?.message || 'Не удалось отправить закуп'
      alert(errorMsg)
    } finally {
      setSubmitting(null)
    }
  }

  const getWeekdayName = (dayIndex: number) => {
    const names = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
    return names[dayIndex] || ''
  }

  // Filter suppliers based on toggle
  const displayedSuppliers = data?.suppliers.filter((s: PurchaseSupplier) => !onlyToday || s.is_order_day) || []
  const activeSupplier = displayedSuppliers.find(s => s.id === activeSupplierId)

  // Switch supplier helper
  const handleSupplierClick = (id: number) => {
    setActiveSupplierId(id)
  }

  // Quick date modifiers
  const changeDateByDays = (days: number) => {
    const current = new Date(date)
    current.setDate(current.getDate() + days)
    setDate(current.toISOString().split('T')[0])
  }

  return (
    <div className="container max-w-lg mx-auto p-4 space-y-4 pb-24">
      {/* Header card */}
      <div className="card-glass p-5 rounded-2xl flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShoppingCart className="h-6 w-6 text-primary" />
            <h1 className="text-xl font-bold">Лист закупа</h1>
          </div>
          <button 
            onClick={() => fetchBlank(date)} 
            disabled={loading}
            className="p-2 rounded-xl hover:bg-muted/50 text-muted-foreground transition-all disabled:opacity-50"
          >
            <RefreshCw className={cn("h-5 w-5", loading && "animate-spin")} />
          </button>
        </div>
        
        {/* Date Selector */}
        <div className="flex items-center justify-between gap-2 mt-1">
          <button 
            onClick={() => changeDateByDays(-1)}
            className="px-3 py-1.5 rounded-lg bg-muted text-xs hover:bg-muted/80 font-medium transition-all"
          >
            Вчера
          </button>
          
          <div className="flex items-center gap-2 bg-muted/30 px-3 py-1.5 rounded-xl border border-border flex-1 justify-center">
            <Calendar className="h-4 w-4 text-muted-foreground" />
            <input 
              type="date" 
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="bg-transparent text-sm font-semibold outline-none w-28 text-center" 
            />
          </div>

          <button 
            onClick={() => changeDateByDays(1)}
            className="px-3 py-1.5 rounded-lg bg-muted text-xs hover:bg-muted/80 font-medium transition-all"
          >
            Завтра
          </button>
        </div>

        {data && (
          <div className="text-xs text-muted-foreground text-center">
            Действует расписание на <b>{getWeekdayName(data.weekday)}</b>
          </div>
        )}
      </div>

      {error && (
        <div className="bg-destructive/10 text-destructive border border-destructive/20 p-4 rounded-xl flex items-center gap-3">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <span className="text-sm font-medium">{error}</span>
        </div>
      )}

      {/* Supplier schedule toggle and supplier buttons */}
      {data && (
        <div className="space-y-3">
          <div className="flex items-center justify-between px-1">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              {onlyToday ? 'По расписанию на сегодня' : 'Все поставщики'}
            </span>
            <button 
              onClick={() => {
                const newVal = !onlyToday
                setOnlyToday(newVal)
                const list = data.suppliers.filter((s: PurchaseSupplier) => !newVal || s.is_order_day)
                if (list.length > 0) {
                  setActiveSupplierId(list[0].id)
                } else {
                  setActiveSupplierId(null)
                }
              }}
              className="flex items-center gap-1.5 text-xs text-primary font-medium hover:underline"
            >
              {onlyToday ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
              {onlyToday ? 'Показать всех' : 'Только расписание'}
            </button>
          </div>

          {/* Supplier badges list */}
          <div className="flex flex-wrap gap-2">
            {displayedSuppliers.map((supplier) => (
              <button
                key={supplier.id}
                onClick={() => handleSupplierClick(supplier.id)}
                className={cn(
                  "px-4 py-2.5 rounded-xl text-xs font-semibold border transition-all flex items-center gap-1.5",
                  activeSupplierId === supplier.id
                    ? "bg-primary border-primary text-primary-foreground shadow-lg shadow-primary/20 scale-[1.03]"
                    : "bg-card-glass border-border text-muted-foreground hover:text-foreground"
                )}
              >
                {supplier.name}
                {supplier.is_order_day && (
                  <span className={cn(
                    "w-1.5 h-1.5 rounded-full",
                    activeSupplierId === supplier.id ? "bg-white" : "bg-primary"
                  )} />
                )}
              </button>
            ))}
            {displayedSuppliers.length === 0 && (
              <div className="text-sm text-muted-foreground italic w-full text-center py-4">
                На этот день закупки не запланированы.
              </div>
            )}
          </div>
        </div>
      )}

      {/* Main Form for active supplier */}
      {activeSupplier && (
        <div className="card-glass rounded-2xl overflow-hidden shadow-xl border border-border">
          {/* Supplier header banner */}
          <div className="bg-primary/5 px-5 py-4 border-b border-border flex items-center justify-between">
            <div>
              <h2 className="font-bold text-base text-foreground">{activeSupplier.name}</h2>
              {activeSupplier.cover_days > 0 ? (
                <p className="text-xs text-muted-foreground mt-0.5">
                  Покрытие закупа на: <b>{activeSupplier.cover_days} дн.</b> (расход + запас)
                </p>
              ) : (
                <p className="text-xs text-muted-foreground mt-0.5">Внеплановый закуп</p>
              )}
            </div>
            
            {activeSupplier.ingredients.length > 0 && (
              <span className="px-2.5 py-1 rounded-full bg-primary/10 text-primary text-[10px] font-bold">
                Позиций: {activeSupplier.ingredients.length}
              </span>
            )}
          </div>

          {/* Ingredients list */}
          <div className="divide-y divide-border">
            {activeSupplier.ingredients.map((ing) => (
              <div key={ing.id} className="p-4 flex flex-col gap-3 hover:bg-muted/10 transition-all">
                
                {/* Item Name & Poster Average */}
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold text-sm">{ing.name}</h3>
                    {ing.avg_daily_consumption > 0 && (
                      <span className="text-[10px] text-muted-foreground">
                        Средний расход: {ing.avg_daily_consumption} / день
                      </span>
                    )}
                  </div>
                  
                  {/* Result display */}
                  <div className="text-right">
                    <span className="text-[10px] text-muted-foreground block font-medium">Заказать</span>
                    <span className={cn(
                      "font-bold text-sm",
                      (ing.order_qty || 0) > 0 ? "text-primary scale-110" : "text-muted-foreground"
                    )}>
                      {ing.order_qty || 0}
                    </span>
                  </div>
                </div>

                {/* Grid Inputs for Target and Actual Stock */}
                <div className="grid grid-cols-2 gap-4">
                  {/* Column 1: Target Stock (Цель) */}
                  <div className="flex flex-col gap-1">
                    <label className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">
                      Цель
                    </label>
                    <div className="flex items-center border border-border rounded-xl overflow-hidden bg-background/50 focus-within:border-primary/50 transition-all">
                      <button
                        type="button"
                        onClick={() => adjustStock(activeSupplier.id, ing.id, 'target', -1)}
                        className="px-2.5 py-1.5 hover:bg-muted text-muted-foreground transition-all"
                      >
                        <Minus className="h-3 w-3" />
                      </button>
                      <input
                        type="text"
                        inputMode="decimal"
                        value={ing.target_stock}
                        onChange={(e) => handleTargetStockChange(activeSupplier.id, ing.id, e.target.value)}
                        className="w-full text-center bg-transparent outline-none text-xs font-bold py-1.5"
                      />
                      <button
                        type="button"
                        onClick={() => adjustStock(activeSupplier.id, ing.id, 'target', 1)}
                        className="px-2.5 py-1.5 hover:bg-muted text-muted-foreground transition-all"
                      >
                        <Plus className="h-3 w-3" />
                      </button>
                    </div>
                  </div>

                  {/* Column 2: Actual Stock (Остаток) */}
                  <div className="flex flex-col gap-1">
                    <label className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">
                      Остаток
                    </label>
                    <div className="flex items-center border border-border rounded-xl overflow-hidden bg-background/50 focus-within:border-primary/50 transition-all">
                      <button
                        type="button"
                        onClick={() => adjustStock(activeSupplier.id, ing.id, 'actual', -1)}
                        className="px-2.5 py-1.5 hover:bg-muted text-muted-foreground transition-all"
                      >
                        <Minus className="h-3 w-3" />
                      </button>
                      <input
                        type="text"
                        inputMode="decimal"
                        placeholder="0"
                        value={ing.actual_stock || ''}
                        onChange={(e) => handleActualStockChange(activeSupplier.id, ing.id, e.target.value)}
                        className="w-full text-center bg-transparent outline-none text-xs font-bold py-1.5 placeholder-muted-foreground/50"
                      />
                      <button
                        type="button"
                        onClick={() => adjustStock(activeSupplier.id, ing.id, 'actual', 1)}
                        className="px-2.5 py-1.5 hover:bg-muted text-muted-foreground transition-all"
                      >
                        <Plus className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                </div>

              </div>
            ))}

            {activeSupplier.ingredients.length === 0 && (
              <div className="p-8 text-center text-sm text-muted-foreground italic">
                В бланке нет ингредиентов для этого поставщика.
              </div>
            )}
          </div>

          {/* Form Submit Footer */}
          {activeSupplier.ingredients.length > 0 && (
            <div className="p-4 bg-muted/20 border-t border-border flex justify-end">
              <button
                onClick={() => handleSubmit(activeSupplier)}
                disabled={submitting !== null}
                className="w-full md:w-auto px-6 py-3 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/95 transition-all flex items-center justify-center gap-2 shadow-lg shadow-primary/20 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {submitting === activeSupplier.id ? (
                  <>
                    <RefreshCw className="h-4 w-4 animate-spin" />
                    Отправка...
                  </>
                ) : (
                  <>
                    <Check className="h-4 w-4" />
                    Сформировать и отправить закуп
                  </>
                )}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function cn(...classes: any[]) {
  return classes.filter(Boolean).join(' ')
}

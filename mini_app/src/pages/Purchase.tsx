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
  RefreshCw
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
  
  const fetchBlank = async (targetDate: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await getApiClient().getPurchaseBlank(targetDate)
      
      // Initialize items inputs with current Poster stock
      const updatedSuppliers = response.suppliers.map((sup: PurchaseSupplier) => ({
        ...sup,
        ingredients: sup.ingredients.map((ing: PurchaseIngredient) => {
          // Pre-fill actual stock with current_stock from Poster
          const actualStockStr = ing.current_stock !== undefined && ing.current_stock !== null 
            ? String(ing.current_stock) 
            : '0'
          const actualStockVal = parseFloat(actualStockStr) || 0
          
          return {
            ...ing,
            actual_stock: actualStockStr,
            order_qty: Math.max(0, Number((ing.target_stock - actualStockVal).toFixed(2)))
          }
        })
      }))
      
      setData({
        ...response,
        suppliers: updatedSuppliers
      })
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
    const cleanVal = val.replace(/[^0-9.-]/g, '') // allow negative for adjustment if needed, but standard is positive
    
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

  const adjustStock = (supplierId: number, ingId: number, amount: number) => {
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
              
              const currentActual = (ing.actual_stock || '') === '' ? 0 : parseFloat(ing.actual_stock || '')
              const newActual = Math.max(0, currentActual + amount)
              const orderQty = Math.max(0, Number((ing.target_stock - newActual).toFixed(2)))
              return {
                ...ing,
                actual_stock: String(newActual),
                order_qty: orderQty
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
      
      alert(`Заказ для ${supplier.name} успешно отправлен в бот! Вы получите готовое сообщение в Telegram для пересылки в WhatsApp.`)
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

      {/* Stacked Suppliers List */}
      {data && data.suppliers && data.suppliers.length > 0 ? (
        <div className="space-y-6">
          {data.suppliers.map((supplier: PurchaseSupplier) => (
            <div key={supplier.id} className="card-glass rounded-2xl overflow-hidden shadow-lg border border-border">
              {/* Supplier Header Banner */}
              <div className="bg-primary/5 px-5 py-4 border-b border-border flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="font-bold text-base text-foreground">{supplier.name}</h2>
                    {supplier.is_order_day && (
                      <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-500 text-[9px] font-bold">
                        Сегодня день заказа
                      </span>
                    )}
                  </div>
                  {supplier.cover_days > 0 ? (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Покрытие закупа на: <b>{supplier.cover_days} дн.</b> (расход + запас)
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground mt-0.5">Внеплановый закуп</p>
                  )}
                </div>
                
                <span className="px-2.5 py-1 rounded-full bg-primary/10 text-primary text-[10px] font-bold">
                  Позиций: {supplier.ingredients.length}
                </span>
              </div>

              {/* Ingredients List */}
              <div className="divide-y divide-border">
                {supplier.ingredients.map((ing: PurchaseIngredient) => (
                  <div key={ing.id} className="p-4 flex flex-col gap-2 hover:bg-muted/5 transition-all">
                    
                    {/* Item Name, consumption and order amount */}
                    <div className="flex items-start justify-between">
                      <div>
                        <h3 className="font-semibold text-sm">{ing.name}</h3>
                        
                        <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 mt-0.5 text-[10px] text-muted-foreground font-medium">
                          <span>Цель: <b className="text-foreground">{ing.target_stock}</b></span>
                          {ing.avg_daily_consumption > 0 && (
                            <>
                              <span className="text-muted-foreground/30">|</span>
                              <span>Расход: {ing.avg_daily_consumption}/дн</span>
                            </>
                          )}
                          {ing.current_stock !== undefined && (
                            <>
                              <span className="text-muted-foreground/30">|</span>
                              <span>В постере: {ing.current_stock}</span>
                            </>
                          )}
                        </div>
                      </div>
                      
                      {/* Order Amount display */}
                      <div className="text-right shrink-0">
                        <span className="text-[10px] text-muted-foreground block font-medium">Заказать</span>
                        <span className={cn(
                          "font-bold text-sm",
                          (ing.order_qty || 0) > 0 ? "text-primary scale-110" : "text-muted-foreground"
                        )}>
                          {ing.order_qty || 0}
                        </span>
                      </div>
                    </div>

                    {/* Actual Stock Input field */}
                    <div className="flex items-center gap-3 mt-1.5">
                      <span className="text-xs text-muted-foreground font-medium">Остаток на складе:</span>
                      
                      <div className="flex items-center border border-border rounded-xl overflow-hidden bg-background/50 focus-within:border-primary/50 transition-all w-36">
                        <button
                          type="button"
                          onClick={() => adjustStock(supplier.id, ing.id, -1)}
                          className="px-2.5 py-1.5 hover:bg-muted text-muted-foreground transition-all"
                        >
                          <Minus className="h-3.5 w-3.5" />
                        </button>
                        
                        <input
                          type="text"
                          inputMode="decimal"
                          placeholder="0"
                          value={ing.actual_stock || ''}
                          onChange={(e) => handleActualStockChange(supplier.id, ing.id, e.target.value)}
                          className="w-full text-center bg-transparent outline-none text-xs font-bold py-1.5 placeholder-muted-foreground/30"
                        />
                        
                        <button
                          type="button"
                          onClick={() => adjustStock(supplier.id, ing.id, 1)}
                          className="px-2.5 py-1.5 hover:bg-muted text-muted-foreground transition-all"
                        >
                          <Plus className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>

                  </div>
                ))}

                {supplier.ingredients.length === 0 && (
                  <div className="p-8 text-center text-sm text-muted-foreground italic">
                    В бланке нет ингредиентов для этого поставщика.
                  </div>
                )}
              </div>

              {/* Submit button for this specific supplier */}
              {supplier.ingredients.length > 0 && (
                <div className="p-4 bg-muted/20 border-t border-border flex justify-end">
                  <button
                    onClick={() => handleSubmit(supplier)}
                    disabled={submitting !== null}
                    className="w-full md:w-auto px-6 py-3 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/95 transition-all flex items-center justify-center gap-2 shadow-lg shadow-primary/20 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {submitting === supplier.id ? (
                      <span className="flex items-center gap-2">
                        <RefreshCw className="h-4 w-4 animate-spin" />
                        Отправка...
                      </span>
                    ) : (
                      <span className="flex items-center gap-2">
                        <Check className="h-4 w-4" />
                        Сформировать и отправить закуп {supplier.name}
                      </span>
                    )}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        !loading && (
          <div className="text-center py-12 card-glass rounded-2xl border border-border">
            <div className="text-5xl mb-4">🛒</div>
            <div className="text-sm text-muted-foreground italic">
              На этот день закупки не запланированы.
            </div>
          </div>
        )
      )}
    </div>
  )
}

function cn(...classes: any[]) {
  return classes.filter(Boolean).join(' ')
}

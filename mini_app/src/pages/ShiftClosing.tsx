import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'
import { formatMoney, parseInputValue, getKzToday, formatDate, toPosterDate } from '../utils/format'
import type { ShiftClosingCalculations } from '../types'

// localStorage key for inputs
function storageKey(date: string): string {
  return `shift_closing_${date}`
}

interface InputState {
  wolt: string
  halyk: string
  kaspi: string
  kaspiCafe: string
  cashBills: string
  cashCoins: string
  shiftStart: string
  deposits: string
  expenses: string
  cashToLeave: string
}

const defaultInputs: InputState = {
  wolt: '', halyk: '', kaspi: '', kaspiCafe: '',
  cashBills: '', cashCoins: '', shiftStart: '',
  deposits: '0', expenses: '', cashToLeave: '15000',
}

export const ShiftClosing: React.FC = () => {
  const navigate = useNavigate()
  const { webApp, themeParams } = useTelegram()

  // Selected date
  const [selectedDate, setSelectedDate] = useState(getKzToday)
  const isToday = selectedDate === getKzToday()

  // History dates
  const [historyDates, setHistoryDates] = useState<string[]>([])

  // View mode: 'edit' for today (or reopen past), 'view' for viewing saved history
  const [viewMode, setViewMode] = useState<'edit' | 'view'>('edit')

  // Load Poster data (only for today or editable dates)
  const { data: posterData, loading, error, refetch } = useApi(() =>
    getApiClient().getShiftClosingPosterData(isToday ? undefined : toPosterDate(selectedDate))
  , [selectedDate])

  // Form state
  const [inputs, setInputs] = useState<InputState>(defaultInputs)
  const [calculations, setCalculations] = useState<ShiftClosingCalculations | null>(null)
  const [calculating, setCalculating] = useState(false)
  const [reportCopied, setReportCopied] = useState(false)
  const [saving, setSaving] = useState(false)
  const [transfersCreated, setTransfersCreated] = useState(false)
  const [transferStatus, setTransferStatus] = useState<string>('')

  // Track if shift_start was auto-set (so we don't overwrite user edits)
  const shiftStartAutoSet = useRef(false)
  const cashierDataAutoSet = useRef(false)
  const [cashierDataSubmitted, setCashierDataSubmitted] = useState(false)

  // Helper to update a single input field
  const setField = useCallback((field: keyof InputState, value: string) => {
    setInputs(prev => ({ ...prev, [field]: value }))
  }, [])

  // Save inputs to localStorage
  const saveToLocalStorage = useCallback((date: string, state: InputState) => {
    try {
      localStorage.setItem(storageKey(date), JSON.stringify(state))
    } catch { /* ignore */ }
  }, [])

  // Load inputs from localStorage
  const loadFromLocalStorage = useCallback((date: string): InputState | null => {
    try {
      const raw = localStorage.getItem(storageKey(date))
      if (raw) return JSON.parse(raw)
    } catch { /* ignore */ }
    return null
  }, [])

  // Load history dates on mount
  useEffect(() => {
    getApiClient().getShiftClosingDates().then(res => {
      if (res.success) setHistoryDates(res.dates)
    }).catch(() => {})
  }, [])

  // When date changes, load saved data or localStorage
  useEffect(() => {
    shiftStartAutoSet.current = false
    setTransfersCreated(false)
    setTransferStatus('')

    // Try to load saved closing from backend
    getApiClient().getShiftClosingHistory(selectedDate).then(res => {
      if (res.success && res.closing) {
        // Restore input values from saved closing
        const c = res.closing
        const restored: InputState = {
          wolt: String(c.wolt || ''),
          halyk: String(c.halyk || ''),
          kaspi: String(c.kaspi || ''),
          kaspiCafe: String(c.kaspi_cafe || ''),
          cashBills: String(c.cash_bills || ''),
          cashCoins: String(c.cash_coins || ''),
          shiftStart: String(c.shift_start || ''),
          deposits: String(c.deposits || '0'),
          expenses: String(c.expenses || ''),
          cashToLeave: String(c.cash_to_leave || '15000'),
        }
        setInputs(restored)
        shiftStartAutoSet.current = true
        setTransfersCreated(!!c.transfers_created)
        setTransferStatus('')
        setViewMode(isToday ? 'edit' : 'view')
      } else {
        // No saved data found
        // Try localStorage
        const local = loadFromLocalStorage(selectedDate)
        if (local) {
          setInputs(local)
          shiftStartAutoSet.current = !!local.shiftStart
        } else {
          setInputs(defaultInputs)
        }
        setViewMode('edit')
      }
    }).catch(() => {
      const local = loadFromLocalStorage(selectedDate)
      if (local) {
        setInputs(local)
      } else {
        setInputs(defaultInputs)
      }
      setViewMode('edit')
    })
  }, [selectedDate, isToday, loadFromLocalStorage])

  // Set shift_start from previous day's shift_left (priority) or Poster
  useEffect(() => {
    if (!posterData || shiftStartAutoSet.current) return

    if (posterData.prev_shift_left != null) {
      // Use previous day's "Смена оставили" as today's shift_start
      setInputs(prev => ({ ...prev, shiftStart: String(posterData.prev_shift_left! / 100) }))
    } else if (posterData.shift_start) {
      // Fallback to Poster cash drawer calculation
      setInputs(prev => ({ ...prev, shiftStart: String(posterData.shift_start / 100) }))
    }
    shiftStartAutoSet.current = true
  }, [posterData])

  // Auto-fill cashier data (wolt, halyk, cash_bills, cash_coins, expenses) if submitted
  useEffect(() => {
    if (!posterData || cashierDataAutoSet.current) return
    if ((posterData as any).cashier_data_submitted) {
      const pd = posterData as any
      setInputs(prev => ({
        ...prev,
        wolt: String(pd.cashier_wolt || prev.wolt || ''),
        halyk: String(pd.cashier_halyk || prev.halyk || ''),
        cashBills: String(pd.cashier_cash_bills || prev.cashBills || ''),
        cashCoins: String(pd.cashier_cash_coins || prev.cashCoins || ''),
        expenses: String(pd.cashier_expenses || prev.expenses || ''),
      }))
      setCashierDataSubmitted(true)
      cashierDataAutoSet.current = true
    }
  }, [posterData])

  // Save inputs to localStorage on every change
  useEffect(() => {
    if (viewMode === 'edit') {
      saveToLocalStorage(selectedDate, inputs)
    }
  }, [inputs, selectedDate, viewMode, saveToLocalStorage])

  // Calculate totals when inputs change
  const calculateTotals = useCallback(async () => {
    if (!posterData) return

    setCalculating(true)

    try {
      const result = await getApiClient().calculateShiftClosing({
        wolt: parseInputValue(inputs.wolt),
        halyk: parseInputValue(inputs.halyk),
        kaspi: parseInputValue(inputs.kaspi),
        kaspi_cafe: parseInputValue(inputs.kaspiCafe),
        cash_bills: parseInputValue(inputs.cashBills),
        cash_coins: parseInputValue(inputs.cashCoins),
        shift_start: parseInputValue(inputs.shiftStart),
        deposits: parseInputValue(inputs.deposits),
        expenses: parseInputValue(inputs.expenses),
        cash_to_leave: parseInputValue(inputs.cashToLeave),
        poster_trade: posterData.trade_total,
        poster_bonus: posterData.bonus,
        poster_card: posterData.poster_card,
      })

      if (result.success) {
        setCalculations(result.calculations)
      }
    } catch (err) {
      console.error('Calculation error:', err)
    } finally {
      setCalculating(false)
    }
  }, [inputs, posterData])

  // Debounced calculation
  useEffect(() => {
    const timer = setTimeout(() => {
      calculateTotals()
    }, 300)
    return () => clearTimeout(timer)
  }, [calculateTotals])

  // Auto-save to backend when calculations change and we have real input
  useEffect(() => {
    if (!calculations || viewMode !== 'edit') return

    const hasInput = parseInputValue(inputs.cashBills) > 0 ||
      parseInputValue(inputs.wolt) > 0 || parseInputValue(inputs.kaspi) > 0 ||
      parseInputValue(inputs.halyk) > 0

    if (!hasInput) return

    const saveTimer = setTimeout(() => {
      setSaving(true)
      getApiClient().saveShiftClosing({
        date: selectedDate,
        wolt: parseInputValue(inputs.wolt),
        halyk: parseInputValue(inputs.halyk),
        kaspi: parseInputValue(inputs.kaspi),
        kaspi_cafe: parseInputValue(inputs.kaspiCafe),
        cash_bills: parseInputValue(inputs.cashBills),
        cash_coins: parseInputValue(inputs.cashCoins),
        shift_start: parseInputValue(inputs.shiftStart),
        deposits: parseInputValue(inputs.deposits),
        expenses: parseInputValue(inputs.expenses),
        cash_to_leave: parseInputValue(inputs.cashToLeave),
        poster_trade: calculations.poster_trade,
        poster_bonus: calculations.poster_bonus,
        poster_card: calculations.poster_card,
        poster_cash: 0,
        transactions_count: posterData?.transactions_count || 0,
        fact_cashless: calculations.fact_cashless,
        fact_total: calculations.fact_total,
        fact_adjusted: calculations.fact_adjusted,
        poster_total: calculations.poster_total,
        day_result: calculations.day_result,
        shift_left: calculations.shift_left,
        collection: calculations.collection,
        cashless_diff: calculations.cashless_diff,
      }).then(() => {
        // Update history dates if this date isn't there yet
        if (!historyDates.includes(selectedDate)) {
          setHistoryDates(prev => [selectedDate, ...prev].sort().reverse())
        }
      }).catch(() => {}).finally(() => setSaving(false))
    }, 1000)

    return () => clearTimeout(saveTimer)
  }, [calculations, viewMode, selectedDate, inputs, posterData, historyDates])

  // Copy report to clipboard and create transfers
  const handleCopyReport = useCallback(async () => {
    try {
      // 1. Copy report
      const res = await getApiClient().getShiftClosingReport(selectedDate)
      if (res.success && res.report) {
        await navigator.clipboard.writeText(res.report)
        setReportCopied(true)
        setTimeout(() => setReportCopied(false), 2000)
      }

      // 2. Create transfers if not already created and in edit mode
      if (!transfersCreated && viewMode === 'edit') {
        try {
          const transferRes = await getApiClient().createShiftClosingTransfers(selectedDate)

          if (transferRes.success) {
            setTransfersCreated(true)
            if (transferRes.already_created) {
              setTransferStatus('Переводы уже были созданы ранее')
            } else if ((transferRes.created_count || 0) > 0) {
              setTransferStatus(`${transferRes.created_count} перевод(а) создано`)
            }
          } else {
            setTransferStatus('Ошибка: ' + (transferRes.error || ''))
          }
        } catch (err) {
          console.error('Transfer error:', err)
        }
      }
    } catch (err) {
      console.error('Report error:', err)
    }
  }, [selectedDate, transfersCreated, viewMode])

  // Setup back button
  useEffect(() => {
    if (!webApp?.BackButton) return

    webApp.BackButton.show()
    const handleBack = () => navigate('/')
    webApp.BackButton.onClick(handleBack)

    return () => {
      webApp.BackButton.offClick(handleBack)
      webApp.BackButton.hide()
    }
  }, [webApp?.BackButton, navigate])

  if (loading) return <Loading />
  if (error) return <ErrorMessage message={error.message} onRetry={refetch} />

  // Build date options for selector: today + history dates
  const today = getKzToday()
  const allDates = Array.from(new Set([today, ...historyDates])).sort().reverse()

  // Day result styling
  const dayResult = calculations?.day_result ?? 0
  const isPositive = dayResult > 0
  const isNegative = dayResult < 0
  const dayResultColor = isPositive ? '#22c55e' : isNegative ? '#ef4444' : themeParams.text_color
  const dayResultLabel = isPositive ? '(излишек)' : isNegative ? '(недостача)' : '(идеально!)'

  const isReadonly = viewMode === 'view'

  const inputStyle = {
    backgroundColor: isReadonly ? (themeParams.secondary_bg_color || '#f3f4f6') : (themeParams.bg_color || '#ffffff'),
    color: themeParams.text_color,
    borderColor: themeParams.hint_color,
  }

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen pb-8">
      <Header title="Закрытие смены" showBack />

      <div className="p-4">
        {/* Date Selector */}
        <div
          className="mb-6 p-3 rounded-lg"
          style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
        >
          <div className="flex items-center gap-2 overflow-x-auto pb-1" style={{ WebkitOverflowScrolling: 'touch' }}>
            {allDates.map(d => (
              <button
                key={d}
                onClick={() => setSelectedDate(d)}
                className="px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap flex-shrink-0"
                style={{
                  backgroundColor: d === selectedDate ? themeParams.button_color : themeParams.bg_color,
                  color: d === selectedDate ? themeParams.button_text_color : themeParams.text_color,
                }}
              >
                {d === today ? `${formatDate(d)} (сегодня)` : formatDate(d)}
              </button>
            ))}
          </div>
          {isReadonly && (
            <div className="mt-2 flex justify-between items-center">
              <span className="text-xs" style={{ color: themeParams.hint_color }}>
                Просмотр сохранённых данных
              </span>
              <button
                onClick={() => setViewMode('edit')}
                className="text-xs px-2 py-1 rounded"
                style={{ backgroundColor: themeParams.button_color, color: themeParams.button_text_color }}
              >
                Редактировать
              </button>
            </div>
          )}
        </div>

        {/* Two Column Layout */}
        <div className="grid grid-cols-1 gap-6">

          {/* Left Column - Manual Input */}
          <div
            className="p-4 rounded-lg"
            style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
          >
            <h3
              className="text-lg font-semibold mb-4"
              style={{ color: themeParams.text_color }}
            >
              Фактические данные
              {cashierDataSubmitted && (
                <span className="ml-2 text-xs px-2 py-0.5 rounded-full" style={{ background: '#34c75920', color: '#30d158' }}>
                  Данные от кассира
                </span>
              )}
            </h3>

            {/* Безнал терминалы */}
            <div className="space-y-3 mb-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                    Wolt
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={inputs.wolt}
                    onChange={(e) => setField('wolt', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={inputStyle}
                  />
                </div>
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                    Halyk
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={inputs.halyk}
                    onChange={(e) => setField('halyk', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={inputStyle}
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Kaspi терминал
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={inputs.kaspi}
                  onChange={(e) => setField('kaspi', e.target.value)}
                  readOnly={isReadonly}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={inputStyle}
                />
              </div>

              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  - Kaspi от PizzBurg-Cafe
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={inputs.kaspiCafe}
                  onChange={(e) => setField('kaspiCafe', e.target.value)}
                  readOnly={isReadonly}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={{
                    ...inputStyle,
                    borderColor: '#f87171',
                  }}
                />
              </div>
            </div>

            {/* Наличка */}
            <div className="space-y-3 mb-4 pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                    Наличка (бумажные)
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={inputs.cashBills}
                    onChange={(e) => setField('cashBills', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={inputStyle}
                  />
                </div>
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                    Наличка (мелочь)
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={inputs.cashCoins}
                    onChange={(e) => setField('cashCoins', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={inputStyle}
                  />
                </div>
              </div>
            </div>

            {/* Фактический итог */}
            {calculations && (
              <div
                className="p-3 rounded-lg mb-4"
                style={{ backgroundColor: themeParams.button_color + '20' }}
              >
                <div className="flex justify-between items-center">
                  <span style={{ color: themeParams.text_color }} className="font-medium">
                    = Фактический:
                  </span>
                  <span style={{ color: themeParams.button_color }} className="text-lg font-bold">
                    {formatMoney(calculations.fact_total)} T
                  </span>
                </div>
              </div>
            )}

            {/* Смена и расходы */}
            <div className="space-y-3 pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Смена (остаток на начало)
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={inputs.shiftStart}
                  onChange={(e) => setField('shiftStart', e.target.value)}
                  readOnly={isReadonly}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={inputStyle}
                />
              </div>

              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Внесение
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={inputs.deposits}
                  onChange={(e) => setField('deposits', e.target.value)}
                  readOnly={isReadonly}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={inputStyle}
                />
              </div>

              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Расход с кассы
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={inputs.expenses}
                  onChange={(e) => setField('expenses', e.target.value)}
                  readOnly={isReadonly}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={inputStyle}
                />
              </div>
            </div>

            {/* Итого фактический */}
            {calculations && (
              <div
                className="p-3 rounded-lg mt-4"
                style={{ backgroundColor: themeParams.button_color + '20' }}
              >
                <div className="flex justify-between items-center">
                  <span style={{ color: themeParams.text_color }} className="font-medium">
                    Итого фактич.:
                  </span>
                  <span style={{ color: themeParams.button_color }} className="text-lg font-bold">
                    {formatMoney(calculations.fact_adjusted)} T
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Right Column - Poster Data & Results */}
          <div
            className="p-4 rounded-lg"
            style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
          >
            <h3
              className="text-lg font-semibold mb-4"
              style={{ color: themeParams.text_color }}
            >
              Данные Poster
            </h3>

            {/* Poster Calculated Values */}
            <div className="space-y-3 mb-4">
              <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                <span style={{ color: themeParams.hint_color }}>Итого безнал:</span>
                <span style={{ color: themeParams.text_color }} className="font-medium">
                  {calculations ? formatMoney(calculations.fact_cashless) : '-'} T
                </span>
              </div>

              <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                <span style={{ color: themeParams.hint_color }}>Картой (Poster):</span>
                <span style={{ color: themeParams.text_color }} className="font-medium">
                  {posterData ? formatMoney(posterData.poster_card / 100) : '-'} T
                </span>
              </div>

              {calculations && Math.abs(calculations.cashless_diff) >= 1 && (
                <div
                  className="flex justify-between items-center p-2 rounded"
                  style={{ backgroundColor: calculations.cashless_diff > 0 ? '#dcfce7' : '#fee2e2' }}
                >
                  <span style={{ color: themeParams.hint_color }}>Разница безнал:</span>
                  <span style={{ color: calculations.cashless_diff > 0 ? '#22c55e' : '#ef4444' }} className="font-medium">
                    {calculations.cashless_diff > 0 ? '+' : ''}{formatMoney(calculations.cashless_diff)} T
                  </span>
                </div>
              )}
            </div>

            {/* Poster API Data */}
            <div className="space-y-3 pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
              <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                <span style={{ color: themeParams.hint_color }}>Poster (торговля):</span>
                <span style={{ color: themeParams.text_color }} className="font-medium">
                  {posterData ? formatMoney(posterData.trade_total / 100) : '-'} T
                </span>
              </div>

              <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                <span style={{ color: themeParams.hint_color }}>Бонусы:</span>
                <span style={{ color: '#ef4444' }} className="font-medium">
                  -{posterData ? formatMoney(posterData.bonus / 100) : '-'} T
                </span>
              </div>

              <div
                className="flex justify-between items-center p-3 rounded"
                style={{ backgroundColor: themeParams.button_color + '20' }}
              >
                <span style={{ color: themeParams.text_color }} className="font-medium">
                  Итого Poster:
                </span>
                <span style={{ color: themeParams.button_color }} className="text-lg font-bold">
                  {calculations ? formatMoney(calculations.poster_total) : '-'} T
                </span>
              </div>
            </div>
          </div>

          {/* Day Result - Big Block */}
          <div
            className="p-6 rounded-lg text-center"
            style={{
              backgroundColor: isPositive ? '#dcfce7' : isNegative ? '#fee2e2' : themeParams.secondary_bg_color,
            }}
          >
            <div className="text-sm mb-2" style={{ color: themeParams.hint_color }}>
              ИТОГО ДЕНЬ
            </div>
            <div
              className="text-4xl font-bold mb-2"
              style={{ color: dayResultColor }}
            >
              {calculations ? (dayResult > 0 ? '+' : '') + formatMoney(dayResult) : '-'} T
            </div>
            <div style={{ color: dayResultColor }} className="text-lg">
              {calculations ? dayResultLabel : ''}
            </div>
          </div>

          {/* Bottom Section - Shift Left & Collection */}
          <div
            className="p-4 rounded-lg"
            style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
          >
            <div className="space-y-4">
              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Оставить на смену (бумажные)
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={inputs.cashToLeave}
                  onChange={(e) => setField('cashToLeave', e.target.value)}
                  readOnly={isReadonly}
                  placeholder="15000"
                  className="w-full p-3 rounded-lg border text-right"
                  style={inputStyle}
                />
              </div>

              {calculations && (
                <>
                  <div className="flex justify-between items-center p-3 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                    <span style={{ color: themeParams.text_color }}>
                      Смена оставили:
                    </span>
                    <span style={{ color: themeParams.text_color }} className="font-bold">
                      {formatMoney(calculations.shift_left)} T
                    </span>
                  </div>
                  <div className="text-xs text-center" style={{ color: themeParams.hint_color }}>
                    ({formatMoney(parseInputValue(inputs.cashToLeave))} бумажные + {formatMoney(parseInputValue(inputs.cashCoins))} мелочь)
                  </div>

                  <div
                    className="flex justify-between items-center p-4 rounded-lg"
                    style={{ backgroundColor: themeParams.button_color }}
                  >
                    <span style={{ color: themeParams.button_text_color }} className="font-medium text-lg">
                      Инкассация:
                    </span>
                    <span style={{ color: themeParams.button_text_color }} className="text-2xl font-bold">
                      {formatMoney(calculations.collection)} T
                    </span>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Report Button */}
          {calculations && (
            <div>
              <button
                onClick={handleCopyReport}
                className="w-full p-4 rounded-lg text-center font-medium"
                style={{
                  backgroundColor: reportCopied ? '#22c55e' : themeParams.secondary_bg_color || '#f3f4f6',
                  color: reportCopied ? '#ffffff' : themeParams.text_color,
                }}
              >
                {reportCopied
                  ? (transfersCreated ? 'Скопировано! (переводы ✓)' : 'Скопировано!')
                  : (transfersCreated ? 'Скопировать отчёт (переводы ✓)' : 'Скопировать отчёт и создать переводы')}
              </button>
              {transferStatus && (
                <div className="text-center text-sm mt-2" style={{ color: '#34c759' }}>
                  {transferStatus}
                </div>
              )}
            </div>
          )}

          {/* Transactions count info */}
          {posterData && (
            <div className="text-center text-sm" style={{ color: themeParams.hint_color }}>
              Закрытых заказов: {posterData.transactions_count}
              {saving && ' | Сохранение...'}
            </div>
          )}
        </div>

        {calculating && (
          <div className="fixed bottom-4 left-4 right-4 p-3 rounded-lg text-center"
            style={{ backgroundColor: themeParams.button_color, color: themeParams.button_text_color }}>
            Пересчёт...
          </div>
        )}
      </div>
    </div>
  )
}

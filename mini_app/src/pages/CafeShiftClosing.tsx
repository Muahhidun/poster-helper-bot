import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import { formatMoney, parseInputValue, getKzToday, formatDate, toPosterDate } from '../utils/format'
import type { CafeSalaryEntry, CafeCalculations } from '../types'

const CAFE_ROLES = ['Кассир', 'Сушист', 'Повар Сандей'] as const

interface SalaryState {
  entries: CafeSalaryEntry[]
}

interface ShiftInputState {
  wolt: string
  kaspi: string
  kaspiPizzburg: string
  cashBills: string
  cashCoins: string
  shiftStart: string
  expenses: string
  cashToLeave: string
}

const defaultSalaryState: SalaryState = {
  entries: CAFE_ROLES.map(role => ({ role, name: '', amount: 0 })),
}

const defaultShiftInputs: ShiftInputState = {
  wolt: '', kaspi: '', kaspiPizzburg: '',
  cashBills: '', cashCoins: '', shiftStart: '',
  expenses: '', cashToLeave: '10000',
}

// localStorage keys
function salaryKey(date: string) { return `cafe_salary_${date}` }
function shiftKey(date: string) { return `cafe_sc_${date}` }

export const CafeShiftClosing: React.FC = () => {
  const navigate = useNavigate()
  const { webApp, themeParams } = useTelegram()

  const today = getKzToday()

  // Step: 1=salaries, 2=shift closing
  const [step, setStep] = useState(1)
  const [loading, setLoading] = useState(true)

  // Date selection (for step 2)
  const [selectedDate, setSelectedDate] = useState(today)
  const isToday = selectedDate === today
  const [historyDates, setHistoryDates] = useState<string[]>([])
  const [viewMode, setViewMode] = useState<'edit' | 'view'>('edit')

  // Salary state
  const [salary, setSalary] = useState<SalaryState>(defaultSalaryState)
  const [salariesCreated, setSalariesCreated] = useState(false)
  const [salaryTotal, setSalaryTotal] = useState(0)

  // Shift input state
  const [inputs, setInputs] = useState<ShiftInputState>(defaultShiftInputs)
  const [calculations, setCalculations] = useState<CafeCalculations | null>(null)
  const [calculating, setCalculating] = useState(false)
  const [saving, setSaving] = useState(false)

  // Poster data
  const { data: posterData, loading: posterLoading, error: posterError, refetch: refetchPoster } = useApi(() =>
    getApiClient().getCafePosterData(isToday ? undefined : toPosterDate(selectedDate))
  , [selectedDate])

  // Report & transfers
  const [reportCopied, setReportCopied] = useState(false)
  const [transfersCreated, setTransfersCreated] = useState(false)
  const [transferStatus, setTransferStatus] = useState('')

  // Status
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const shiftStartAutoSet = useRef(false)

  // Calculate salary total
  useEffect(() => {
    setSalaryTotal(salary.entries.reduce((s, e) => s + (e.amount || 0), 0))
  }, [salary])

  // Load initial data
  useEffect(() => {
    const init = async () => {
      try {
        const api = getApiClient()

        // Check salary status
        const status = await api.getCafeSalaryStatus()
        if (status.success && status.salaries_created) {
          setSalariesCreated(true)
          if (status.salaries_data) {
            setSalary({ entries: status.salaries_data })
          }
          setStep(2)
        } else {
          // Load last employees for auto-fill
          try {
            const last = await api.getCafeEmployeesLast()
            if (last.success && last.employees?.length > 0) {
              setSalary({ entries: last.employees })
            }
          } catch { /* ignore */ }
          // Try localStorage
          try {
            const saved = localStorage.getItem(salaryKey(today))
            if (saved) setSalary(JSON.parse(saved))
          } catch { /* ignore */ }
        }

        // Load history dates
        try {
          const dates = await api.getCafeShiftDates()
          if (dates.success) setHistoryDates(dates.dates)
        } catch { /* ignore */ }
      } catch (err) {
        console.error('Init error:', err)
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [today])

  // When date changes, load saved data
  useEffect(() => {
    if (step < 2) return

    shiftStartAutoSet.current = false
    setTransfersCreated(false)
    setTransferStatus('')

    getApiClient().getCafeShiftHistory(selectedDate).then(res => {
      if (res.success && res.closing) {
        const c = res.closing
        setInputs({
          wolt: String(c.wolt || ''),
          kaspi: String(c.kaspi || ''),
          kaspiPizzburg: String(c.kaspi_pizzburg || ''),
          cashBills: String(c.cash_bills || ''),
          cashCoins: String(c.cash_coins || ''),
          shiftStart: String(c.shift_start || ''),
          expenses: String(c.expenses || ''),
          cashToLeave: String(c.cash_to_leave || '10000'),
        })
        shiftStartAutoSet.current = true
        setTransfersCreated(!!c.transfers_created)
        setViewMode(isToday ? 'edit' : 'view')
      } else {
        // Try localStorage
        try {
          const saved = localStorage.getItem(shiftKey(selectedDate))
          if (saved) {
            setInputs(JSON.parse(saved))
            shiftStartAutoSet.current = true
          } else {
            setInputs(defaultShiftInputs)
          }
        } catch {
          setInputs(defaultShiftInputs)
        }
        setViewMode('edit')
      }
    }).catch(() => {
      try {
        const saved = localStorage.getItem(shiftKey(selectedDate))
        if (saved) setInputs(JSON.parse(saved))
        else setInputs(defaultShiftInputs)
      } catch {
        setInputs(defaultShiftInputs)
      }
      setViewMode('edit')
    })
  }, [selectedDate, step, isToday])

  // Set shift_start from previous day or Poster
  useEffect(() => {
    if (!posterData || shiftStartAutoSet.current || step < 2) return

    if (posterData.prev_shift_left != null) {
      setInputs(prev => ({ ...prev, shiftStart: String(posterData.prev_shift_left! / 100) }))
    } else if (posterData.shift_start) {
      setInputs(prev => ({ ...prev, shiftStart: String(posterData.shift_start / 100) }))
    }

    // Auto-fill kaspi_pizzburg from main cafe if available
    if (posterData.main_kaspi_cafe != null && posterData.main_kaspi_cafe > 0) {
      setInputs(prev => {
        if (!prev.kaspiPizzburg) return { ...prev, kaspiPizzburg: String(posterData.main_kaspi_cafe) }
        return prev
      })
    }

    shiftStartAutoSet.current = true
  }, [posterData, step])

  // Save salary to localStorage
  useEffect(() => {
    if (step === 1 && !salariesCreated) {
      try { localStorage.setItem(salaryKey(today), JSON.stringify(salary)) } catch { /* ignore */ }
    }
  }, [salary, step, salariesCreated, today])

  // Save shift inputs to localStorage
  useEffect(() => {
    if (step === 2 && viewMode === 'edit') {
      try { localStorage.setItem(shiftKey(selectedDate), JSON.stringify(inputs)) } catch { /* ignore */ }
    }
  }, [inputs, step, viewMode, selectedDate])

  // Salary handlers
  const setSalaryName = useCallback((index: number, name: string) => {
    setSalary(prev => {
      const entries = [...prev.entries]
      entries[index] = { ...entries[index], name }
      return { entries }
    })
  }, [])

  const setSalaryAmount = useCallback((index: number, value: string) => {
    setSalary(prev => {
      const entries = [...prev.entries]
      entries[index] = { ...entries[index], amount: parseInputValue(value) }
      return { entries }
    })
  }, [])

  // Create salaries
  const handleCreateSalaries = useCallback(async () => {
    const emptyNames = salary.entries.some(e => !e.name.trim())
    const zeroAmounts = salary.entries.every(e => e.amount === 0)
    if (emptyNames) {
      setError('Заполните все имена')
      return
    }
    if (zeroAmounts) {
      setError('Введите хотя бы одну сумму')
      return
    }
    setError(null)
    setSubmitting(true)

    try {
      const result = await getApiClient().createCafeSalaries({
        salaries: salary.entries.map(e => ({ role: e.role, name: e.name.trim(), amount: e.amount })),
      })

      if (result.success) {
        setSalariesCreated(true)
        setSalaryTotal(result.total)
        if (result.warning) {
          setError(result.warning)
        }
        setStep(2)
        webApp?.HapticFeedback?.notificationOccurred('success')
      }
    } catch (err) {
      setError('Ошибка создания зарплат')
      console.error(err)
    } finally {
      setSubmitting(false)
    }
  }, [salary, webApp])

  // Shift input setter
  const setField = useCallback((field: keyof ShiftInputState, value: string) => {
    setInputs(prev => ({ ...prev, [field]: value }))
  }, [])

  // Calculate totals
  const calculateTotals = useCallback(async () => {
    if (!posterData) return
    setCalculating(true)

    try {
      const result = await getApiClient().calculateCafeShift({
        wolt: parseInputValue(inputs.wolt),
        kaspi: parseInputValue(inputs.kaspi),
        kaspi_pizzburg: parseInputValue(inputs.kaspiPizzburg),
        cash_bills: parseInputValue(inputs.cashBills),
        cash_coins: parseInputValue(inputs.cashCoins),
        shift_start: parseInputValue(inputs.shiftStart),
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
      console.error('Calc error:', err)
    } finally {
      setCalculating(false)
    }
  }, [inputs, posterData])

  // Debounced calculation
  useEffect(() => {
    if (step < 2) return
    const timer = setTimeout(() => calculateTotals(), 300)
    return () => clearTimeout(timer)
  }, [calculateTotals, step])

  // Auto-save to backend
  useEffect(() => {
    if (!calculations || viewMode !== 'edit' || step < 2 || !posterData) return

    const hasInput = parseInputValue(inputs.cashBills) > 0 ||
      parseInputValue(inputs.wolt) > 0 || parseInputValue(inputs.kaspi) > 0

    if (!hasInput) return

    const saveTimer = setTimeout(() => {
      setSaving(true)
      getApiClient().saveCafeShift({
        date: selectedDate,
        wolt: parseInputValue(inputs.wolt),
        kaspi: parseInputValue(inputs.kaspi),
        kaspi_pizzburg: parseInputValue(inputs.kaspiPizzburg),
        cash_bills: parseInputValue(inputs.cashBills),
        cash_coins: parseInputValue(inputs.cashCoins),
        shift_start: parseInputValue(inputs.shiftStart),
        expenses: parseInputValue(inputs.expenses),
        cash_to_leave: parseInputValue(inputs.cashToLeave),
        deposits: 0,
        poster_trade: posterData.trade_total,
        poster_bonus: posterData.bonus,
        poster_card: posterData.poster_card,
        poster_cash: 0,
        transactions_count: posterData.transactions_count,
        fact_cashless: calculations.fact_cashless,
        fact_total: calculations.fact_total,
        fact_adjusted: calculations.fact_adjusted,
        poster_total: calculations.poster_total,
        day_result: calculations.day_result,
        shift_left: calculations.shift_left,
        collection: calculations.collection,
        cashless_diff: calculations.cashless_diff,
      }).then(() => {
        if (!historyDates.includes(selectedDate)) {
          setHistoryDates(prev => [selectedDate, ...prev].sort().reverse())
        }
      }).catch(() => {}).finally(() => setSaving(false))
    }, 1000)

    return () => clearTimeout(saveTimer)
  }, [calculations, viewMode, step, selectedDate, inputs, posterData, historyDates])

  // Copy report and create transfers
  const handleCopyReport = useCallback(async () => {
    try {
      const res = await getApiClient().getCafeReport(selectedDate)
      if (res.success && res.report) {
        await navigator.clipboard.writeText(res.report)
        setReportCopied(true)
        setTimeout(() => setReportCopied(false), 2000)
      }

      if (!transfersCreated && viewMode === 'edit') {
        try {
          const transferRes = await getApiClient().createCafeTransfers(selectedDate)
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

  // Back button
  useEffect(() => {
    if (!webApp?.BackButton) return
    webApp.BackButton.show()
    const handleBack = () => {
      if (step === 2 && !salariesCreated) {
        setStep(1)
      } else {
        navigate('/')
      }
    }
    webApp.BackButton.onClick(handleBack)
    return () => {
      webApp.BackButton.offClick(handleBack)
      webApp.BackButton.hide()
    }
  }, [webApp?.BackButton, navigate, step, salariesCreated])

  if (loading) return <Loading />

  const inputStyle = {
    backgroundColor: themeParams.bg_color || '#ffffff',
    color: themeParams.text_color,
    borderColor: themeParams.hint_color,
  }

  const readonlyStyle = {
    ...inputStyle,
    backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
  }

  const isReadonly = viewMode === 'view'

  // Day result styling
  const dayResult = calculations?.day_result ?? 0
  const isPositive = dayResult > 0
  const isNegative = dayResult < 0
  const dayResultColor = isPositive ? '#22c55e' : isNegative ? '#ef4444' : themeParams.text_color
  const dayResultLabel = isPositive ? '(излишек)' : isNegative ? '(недостача)' : '(идеально!)'

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen pb-8">
      <Header title="Закрытие смены — Кафе" showBack />

      <div className="p-4">
        {error && (
          <div className="mb-4 p-3 rounded-lg text-center" style={{ backgroundColor: '#fee2e2', color: '#ef4444' }}>
            {error}
          </div>
        )}

        {/* ======================== */}
        {/* STEP 1: Salaries */}
        {/* ======================== */}
        {step === 1 && (
          <div className="space-y-4">
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <h3 className="text-lg font-semibold mb-4" style={{ color: themeParams.text_color }}>
                Зарплаты
              </h3>

              <div className="space-y-4">
                {salary.entries.map((entry, i) => (
                  <div key={i}>
                    <label className="block text-sm mb-2 font-medium" style={{ color: themeParams.text_color }}>
                      {entry.role}
                    </label>
                    <div className="grid grid-cols-2 gap-2">
                      <input
                        type="text"
                        value={entry.name}
                        onChange={(e) => setSalaryName(i, e.target.value)}
                        placeholder="Имя"
                        className="p-3 rounded-lg border"
                        style={inputStyle}
                      />
                      <input
                        type="text"
                        inputMode="numeric"
                        value={entry.amount || ''}
                        onChange={(e) => setSalaryAmount(i, e.target.value)}
                        placeholder="Сумма"
                        className="p-3 rounded-lg border text-right"
                        style={inputStyle}
                      />
                    </div>
                  </div>
                ))}
              </div>

              {/* Total */}
              <div
                className="mt-4 p-3 rounded-lg flex justify-between items-center"
                style={{ backgroundColor: themeParams.button_color + '20' }}
              >
                <span className="font-medium" style={{ color: themeParams.text_color }}>Итого:</span>
                <span className="font-bold text-lg" style={{ color: themeParams.button_color }}>
                  {formatMoney(salaryTotal)} T
                </span>
              </div>
            </div>

            {/* Next button */}
            <button
              onClick={handleCreateSalaries}
              disabled={submitting}
              className="w-full p-4 rounded-lg font-medium text-lg"
              style={{
                backgroundColor: themeParams.button_color,
                color: themeParams.button_text_color,
                opacity: submitting ? 0.6 : 1,
              }}
            >
              {submitting ? 'Создание...' : 'Далее'}
            </button>
          </div>
        )}

        {/* ======================== */}
        {/* STEP 2: Shift Closing */}
        {/* ======================== */}
        {step === 2 && (
          <div className="space-y-4">
            {/* Salary summary */}
            {salariesCreated && (
              <div className="p-3 rounded-lg flex justify-between items-center" style={{ backgroundColor: '#dcfce7' }}>
                <span style={{ color: '#16a34a' }}>Зарплаты созданы</span>
                <span className="font-bold" style={{ color: '#16a34a' }}>{formatMoney(salaryTotal)} T</span>
              </div>
            )}

            {/* Date Selector */}
            <div
              className="p-3 rounded-lg"
              style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
            >
              <div className="flex items-center gap-2 overflow-x-auto pb-1" style={{ WebkitOverflowScrolling: 'touch' }}>
                {Array.from(new Set([today, ...historyDates])).sort().reverse().map(d => (
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
                  <span className="text-xs" style={{ color: themeParams.hint_color }}>Просмотр</span>
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

            {/* Input Form */}
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <h3 className="text-lg font-semibold mb-4" style={{ color: themeParams.text_color }}>
                Фактические данные
              </h3>

              {/* Безнал */}
              <div className="space-y-3 mb-4">
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Wolt</label>
                  <input
                    type="text" inputMode="numeric"
                    value={inputs.wolt}
                    onChange={(e) => setField('wolt', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={isReadonly ? readonlyStyle : inputStyle}
                  />
                </div>

                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Kaspi</label>
                  <input
                    type="text" inputMode="numeric"
                    value={inputs.kaspi}
                    onChange={(e) => setField('kaspi', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={isReadonly ? readonlyStyle : inputStyle}
                  />
                </div>

                <div>
                  <label className="block text-sm mb-1" style={{ color: '#22c55e' }}>
                    + Kaspi Pizzburg (доставки)
                  </label>
                  <input
                    type="text" inputMode="numeric"
                    value={inputs.kaspiPizzburg}
                    onChange={(e) => setField('kaspiPizzburg', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={{
                      ...(isReadonly ? readonlyStyle : inputStyle),
                      borderColor: '#22c55e',
                    }}
                  />
                </div>
              </div>

              {/* Наличка */}
              <div className="space-y-3 mb-4 pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Бумажные</label>
                    <input
                      type="text" inputMode="numeric"
                      value={inputs.cashBills}
                      onChange={(e) => setField('cashBills', e.target.value)}
                      readOnly={isReadonly}
                      placeholder="0"
                      className="w-full p-3 rounded-lg border text-right"
                      style={isReadonly ? readonlyStyle : inputStyle}
                    />
                  </div>
                  <div>
                    <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Мелочь</label>
                    <input
                      type="text" inputMode="numeric"
                      value={inputs.cashCoins}
                      onChange={(e) => setField('cashCoins', e.target.value)}
                      readOnly={isReadonly}
                      placeholder="0"
                      className="w-full p-3 rounded-lg border text-right"
                      style={isReadonly ? readonlyStyle : inputStyle}
                    />
                  </div>
                </div>
              </div>

              {/* Фактический итог */}
              {calculations && (
                <div className="p-3 rounded-lg mb-4" style={{ backgroundColor: themeParams.button_color + '20' }}>
                  <div className="flex justify-between items-center">
                    <span style={{ color: themeParams.text_color }} className="font-medium">= Фактический:</span>
                    <span style={{ color: themeParams.button_color }} className="text-lg font-bold">
                      {formatMoney(calculations.fact_total)} T
                    </span>
                  </div>
                </div>
              )}

              {/* Смена и расходы */}
              <div className="space-y-3 pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Смена (начало)</label>
                  <input
                    type="text" inputMode="numeric"
                    value={inputs.shiftStart}
                    onChange={(e) => setField('shiftStart', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={isReadonly ? readonlyStyle : inputStyle}
                  />
                </div>
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Расходы</label>
                  <input
                    type="text" inputMode="numeric"
                    value={inputs.expenses}
                    onChange={(e) => setField('expenses', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={isReadonly ? readonlyStyle : inputStyle}
                  />
                </div>
              </div>

              {/* Итого фактический */}
              {calculations && (
                <div className="p-3 rounded-lg mt-4" style={{ backgroundColor: themeParams.button_color + '20' }}>
                  <div className="flex justify-between items-center">
                    <span style={{ color: themeParams.text_color }} className="font-medium">Итого фактич.:</span>
                    <span style={{ color: themeParams.button_color }} className="text-lg font-bold">
                      {formatMoney(calculations.fact_adjusted)} T
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Poster Data */}
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-lg font-semibold" style={{ color: themeParams.text_color }}>Данные Poster</h3>
                <button
                  onClick={() => refetchPoster()}
                  className="text-xs px-3 py-1.5 rounded-lg"
                  style={{ backgroundColor: themeParams.button_color, color: themeParams.button_text_color }}
                >
                  Обновить
                </button>
              </div>

              {posterLoading ? (
                <div className="text-center py-4" style={{ color: themeParams.hint_color }}>Загрузка...</div>
              ) : posterError ? (
                <div className="text-center py-4" style={{ color: '#ef4444' }}>Ошибка загрузки</div>
              ) : (
                <div className="space-y-3">
                  <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                    <span style={{ color: themeParams.hint_color }}>Итого безнал:</span>
                    <span className="font-medium" style={{ color: themeParams.text_color }}>
                      {calculations ? formatMoney(calculations.fact_cashless) : '-'} T
                    </span>
                  </div>

                  <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                    <span style={{ color: themeParams.hint_color }}>Картой (Poster):</span>
                    <span className="font-medium" style={{ color: themeParams.text_color }}>
                      {posterData ? formatMoney(posterData.poster_card / 100) : '-'} T
                    </span>
                  </div>

                  {calculations && Math.abs(calculations.cashless_diff) >= 1 && (
                    <div className="flex justify-between items-center p-2 rounded"
                      style={{ backgroundColor: calculations.cashless_diff > 0 ? '#dcfce7' : '#fee2e2' }}>
                      <span style={{ color: themeParams.hint_color }}>Разница безнал:</span>
                      <span className="font-medium" style={{ color: calculations.cashless_diff > 0 ? '#22c55e' : '#ef4444' }}>
                        {calculations.cashless_diff > 0 ? '+' : ''}{formatMoney(calculations.cashless_diff)} T
                      </span>
                    </div>
                  )}

                  <div className="pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
                    <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                      <span style={{ color: themeParams.hint_color }}>Торговля:</span>
                      <span className="font-medium" style={{ color: themeParams.text_color }}>
                        {posterData ? formatMoney(posterData.trade_total / 100) : '-'} T
                      </span>
                    </div>

                    <div className="flex justify-between items-center p-2 rounded mt-2" style={{ backgroundColor: themeParams.bg_color }}>
                      <span style={{ color: themeParams.hint_color }}>Бонусы:</span>
                      <span className="font-medium" style={{ color: '#ef4444' }}>
                        -{posterData ? formatMoney(posterData.bonus / 100) : '-'} T
                      </span>
                    </div>

                    <div className="flex justify-between items-center p-3 rounded mt-2" style={{ backgroundColor: themeParams.button_color + '20' }}>
                      <span className="font-medium" style={{ color: themeParams.text_color }}>Итого Poster:</span>
                      <span className="text-lg font-bold" style={{ color: themeParams.button_color }}>
                        {calculations ? formatMoney(calculations.poster_total) : '-'} T
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Day Result */}
            <div
              className="p-6 rounded-lg text-center"
              style={{
                backgroundColor: isPositive ? '#dcfce7' : isNegative ? '#fee2e2' : themeParams.secondary_bg_color,
              }}
            >
              <div className="text-sm mb-2" style={{ color: themeParams.hint_color }}>ИТОГО ДЕНЬ</div>
              <div className="text-4xl font-bold mb-2" style={{ color: dayResultColor }}>
                {calculations ? (dayResult > 0 ? '+' : '') + formatMoney(dayResult) : '-'} T
              </div>
              <div style={{ color: dayResultColor }} className="text-lg">
                {calculations ? dayResultLabel : ''}
              </div>
            </div>

            {/* Collection */}
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                    Оставить на смену
                  </label>
                  <input
                    type="text" inputMode="numeric"
                    value={inputs.cashToLeave}
                    onChange={(e) => setField('cashToLeave', e.target.value)}
                    readOnly={isReadonly}
                    placeholder="10000"
                    className="w-full p-3 rounded-lg border text-right"
                    style={isReadonly ? readonlyStyle : inputStyle}
                  />
                </div>

                {calculations && (
                  <>
                    <div className="flex justify-between items-center p-3 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                      <span style={{ color: themeParams.text_color }}>Смена оставили:</span>
                      <span className="font-bold" style={{ color: themeParams.text_color }}>
                        {formatMoney(calculations.shift_left)} T
                      </span>
                    </div>
                    <div className="text-xs text-center" style={{ color: themeParams.hint_color }}>
                      ({formatMoney(parseInputValue(inputs.cashToLeave))} бумажные + {formatMoney(parseInputValue(inputs.cashCoins))} мелочь)
                    </div>

                    <div className="flex justify-between items-center p-4 rounded-lg" style={{ backgroundColor: themeParams.button_color }}>
                      <span className="font-medium text-lg" style={{ color: themeParams.button_text_color }}>Инкассация:</span>
                      <span className="text-2xl font-bold" style={{ color: themeParams.button_text_color }}>
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
                    ? (transfersCreated ? 'Скопировано! (переводы \u2713)' : 'Скопировано!')
                    : (transfersCreated ? 'Скопировать отчёт (переводы \u2713)' : 'Скопировать отчёт и создать переводы')}
                </button>
                {transferStatus && (
                  <div className="text-center text-sm mt-2" style={{ color: '#34c759' }}>
                    {transferStatus}
                  </div>
                )}
              </div>
            )}

            {/* Info */}
            {posterData && (
              <div className="text-center text-sm" style={{ color: themeParams.hint_color }}>
                Заказов: {posterData.transactions_count}
                {saving && ' | Сохранение...'}
              </div>
            )}
          </div>
        )}

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

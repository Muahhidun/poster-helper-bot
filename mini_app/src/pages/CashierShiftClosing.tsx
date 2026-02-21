import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import { formatMoney, parseInputValue, getKzToday } from '../utils/format'
import type { CashierSalaryDetail } from '../types'

type AssistantStartTime = '10:00' | '12:00' | '14:00'

interface SalaryInputState {
  cashierCount: 2 | 3
  cashierNames: string[]
  donerName: string
  assistantName: string
  assistantStartTime: AssistantStartTime
}

interface ShiftDataState {
  wolt: string
  halyk: string
  cashBills: string
  cashCoins: string
  expenses: string
}

const defaultSalaryInput: SalaryInputState = {
  cashierCount: 2,
  cashierNames: ['', ''],
  donerName: '',
  assistantName: '',
  assistantStartTime: '10:00',
}

const defaultShiftData: ShiftDataState = {
  wolt: '',
  halyk: '',
  cashBills: '',
  cashCoins: '',
  expenses: '',
}

// localStorage keys
function salaryStorageKey(date: string): string {
  return `cashier_salary_${date}`
}
function shiftStorageKey(date: string): string {
  return `cashier_shift_${date}`
}

export const CashierShiftClosing: React.FC = () => {
  const navigate = useNavigate()
  const { webApp, themeParams } = useTelegram()

  const today = getKzToday()

  // Step: 1=salary input, 2=salary confirm, 3=shift data, 4=done
  const [step, setStep] = useState(1)
  const [isOwnerView] = useState(false)
  const [loading, setLoading] = useState(true)

  // Salary input state
  const [salaryInput, setSalaryInput] = useState<SalaryInputState>(defaultSalaryInput)

  // Calculated salaries (from API)
  const [confirmedSalaries, setConfirmedSalaries] = useState<CashierSalaryDetail[]>([])
  const [salaryTotal, setSalaryTotal] = useState(0)

  // Shift data state
  const [shiftData, setShiftData] = useState<ShiftDataState>(defaultShiftData)

  // Status flags
  const [salariesCreated, setSalariesCreated] = useState(false)
  const [shiftDataSubmitted, setShiftDataSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load initial data
  useEffect(() => {
    const loadInitialData = async () => {
      try {
        const api = getApiClient()

        // Check status first
        const status = await api.getCashierShiftDataStatus()
        if (status.success) {
          if (status.salaries_created && status.shift_data_submitted) {
            // Everything done — go to step 4
            setSalariesCreated(true)
            setShiftDataSubmitted(true)
            if (status.salaries_data) {
              try {
                const parsed = JSON.parse(status.salaries_data)
                setConfirmedSalaries(parsed)
                setSalaryTotal(parsed.reduce((s: number, e: CashierSalaryDetail) => s + e.salary, 0))
              } catch { /* ignore */ }
            }
            if (status.shift_data) {
              setShiftData({
                wolt: String(status.shift_data.wolt || ''),
                halyk: String(status.shift_data.halyk || ''),
                cashBills: String(status.shift_data.cash_bills || ''),
                cashCoins: String(status.shift_data.cash_coins || ''),
                expenses: String(status.shift_data.expenses || ''),
              })
            }
            setStep(4)
          } else if (status.salaries_created) {
            // Salaries done, need shift data
            setSalariesCreated(true)
            if (status.salaries_data) {
              try {
                const parsed = JSON.parse(status.salaries_data)
                setConfirmedSalaries(parsed)
                setSalaryTotal(parsed.reduce((s: number, e: CashierSalaryDetail) => s + e.salary, 0))
              } catch { /* ignore */ }
            }
            // Load shift data from localStorage
            try {
              const saved = localStorage.getItem(shiftStorageKey(today))
              if (saved) setShiftData(JSON.parse(saved))
            } catch { /* ignore */ }
            setStep(3)
          }
        }

        // Load last employee names for auto-fill
        if (!status.salaries_created) {
          try {
            const last = await api.getCashierEmployeesLast()
            if (last.success) {
              const names = last.cashier_names ? JSON.parse(last.cashier_names) : []
              setSalaryInput(prev => ({
                ...prev,
                cashierCount: (last.cashier_count === 3 ? 3 : 2) as 2 | 3,
                cashierNames: last.cashier_count === 3
                  ? [names[0] || '', names[1] || '', names[2] || '']
                  : [names[0] || '', names[1] || ''],
                donerName: last.doner_name || '',
                assistantName: last.assistant_name || '',
                assistantStartTime: (last.assistant_start_time || '10:00') as AssistantStartTime,
              }))
            }
          } catch { /* ignore */ }
          // Also try localStorage
          try {
            const saved = localStorage.getItem(salaryStorageKey(today))
            if (saved) {
              const parsed = JSON.parse(saved)
              setSalaryInput(prev => ({ ...prev, ...parsed }))
            }
          } catch { /* ignore */ }
        }
      } catch (err) {
        console.error('Init error:', err)
      } finally {
        setLoading(false)
      }
    }

    loadInitialData()
  }, [today])

  // Save salary input to localStorage
  useEffect(() => {
    if (step === 1 && !salariesCreated) {
      try {
        localStorage.setItem(salaryStorageKey(today), JSON.stringify(salaryInput))
      } catch { /* ignore */ }
    }
  }, [salaryInput, step, salariesCreated, today])

  // Save shift data to localStorage
  useEffect(() => {
    if (step === 3 && !shiftDataSubmitted) {
      try {
        localStorage.setItem(shiftStorageKey(today), JSON.stringify(shiftData))
      } catch { /* ignore */ }
    }
  }, [shiftData, step, shiftDataSubmitted, today])

  // Handle cashier count change
  const setCashierCount = useCallback((count: 2 | 3) => {
    setSalaryInput(prev => {
      const names = count === 3
        ? [prev.cashierNames[0] || '', prev.cashierNames[1] || '', prev.cashierNames[2] || '']
        : [prev.cashierNames[0] || '', prev.cashierNames[1] || '']
      return { ...prev, cashierCount: count, cashierNames: names }
    })
  }, [])

  // Handle name change
  const setCashierName = useCallback((index: number, name: string) => {
    setSalaryInput(prev => {
      const names = [...prev.cashierNames]
      names[index] = name
      return { ...prev, cashierNames: names }
    })
  }, [])

  // Step 1 → 2: Calculate salaries
  const handleCalculateSalaries = useCallback(async () => {
    // Validate names
    const emptyNames = salaryInput.cashierNames.some(n => !n.trim())
    if (emptyNames || !salaryInput.donerName.trim() || !salaryInput.assistantName.trim()) {
      setError('Заполните все имена')
      return
    }
    setError(null)
    setSubmitting(true)

    try {
      const result = await getApiClient().calculateCashierSalaries({
        cashier_count: salaryInput.cashierCount,
        assistant_start_time: salaryInput.assistantStartTime,
      })

      if (result.success) {
        // Build salary details for display
        const details: CashierSalaryDetail[] = []
        for (const name of salaryInput.cashierNames) {
          details.push({ name: name.trim(), role: 'Кассир', salary: result.cashier_salary })
        }
        details.push({ name: salaryInput.assistantName.trim(), role: 'Помощник', salary: result.assistant_salary })
        details.push({ name: salaryInput.donerName.trim(), role: 'Донерщик', salary: result.doner_salary })
        setConfirmedSalaries(details)
        setSalaryTotal(details.reduce((s, d) => s + d.salary, 0))
        setStep(2)
      }
    } catch (err) {
      setError('Ошибка расчёта зарплат')
      console.error(err)
    } finally {
      setSubmitting(false)
    }
  }, [salaryInput])

  // Step 2 → 3: Confirm and create salaries
  const handleConfirmSalaries = useCallback(async () => {
    setSubmitting(true)
    setError(null)

    try {
      const result = await getApiClient().createCashierSalaries({
        cashier_count: salaryInput.cashierCount,
        cashier_names: salaryInput.cashierNames.map(n => n.trim()),
        assistant_start_time: salaryInput.assistantStartTime,
        doner_name: salaryInput.donerName.trim(),
        assistant_name: salaryInput.assistantName.trim(),
      })

      if (result.success) {
        setSalariesCreated(true)
        setConfirmedSalaries(result.salaries)
        setSalaryTotal(result.total)
        setStep(3)
        webApp?.HapticFeedback?.notificationOccurred('success')
      }
    } catch (err) {
      setError('Ошибка создания зарплат')
      console.error(err)
    } finally {
      setSubmitting(false)
    }
  }, [salaryInput, webApp])

  // Step 3 → 4: Submit shift data
  const handleSubmitShiftData = useCallback(async () => {
    const wolt = parseInputValue(shiftData.wolt)
    const halyk = parseInputValue(shiftData.halyk)
    const cashBills = parseInputValue(shiftData.cashBills)
    const cashCoins = parseInputValue(shiftData.cashCoins)
    const expenses = parseInputValue(shiftData.expenses)

    if (wolt === 0 && halyk === 0 && cashBills === 0 && cashCoins === 0) {
      setError('Введите хотя бы одно значение')
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      const result = await getApiClient().saveCashierShiftData({
        wolt, halyk, cash_bills: cashBills, cash_coins: cashCoins, expenses,
      })

      if (result.success) {
        setShiftDataSubmitted(true)
        setStep(4)
        webApp?.HapticFeedback?.notificationOccurred('success')
      }
    } catch (err) {
      setError('Ошибка отправки данных')
      console.error(err)
    } finally {
      setSubmitting(false)
    }
  }, [shiftData, webApp])

  // Shift data field setter
  const setShiftField = useCallback((field: keyof ShiftDataState, value: string) => {
    setShiftData(prev => ({ ...prev, [field]: value }))
  }, [])

  // Back button
  useEffect(() => {
    if (!webApp?.BackButton) return
    webApp.BackButton.show()
    const handleBack = () => {
      if (step > 1 && !salariesCreated) {
        setStep(prev => prev - 1)
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

  // ========================
  // Owner readonly view — all steps at once
  // ========================
  if (isOwnerView) {
    return (
      <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen pb-8">
        <Header title="Кассир — Просмотр" showBack />
        <div className="p-4 space-y-4">
          {/* Salary info */}
          {confirmedSalaries.length > 0 && (
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <h3 className="text-lg font-semibold mb-3" style={{ color: themeParams.text_color }}>Зарплаты</h3>
              {confirmedSalaries.map((s, i) => (
                <div key={i} className="flex justify-between items-center py-2 border-b last:border-0" style={{ borderColor: themeParams.hint_color }}>
                  <span style={{ color: themeParams.text_color }}>{s.name} <span className="text-sm" style={{ color: themeParams.hint_color }}>({s.role})</span></span>
                  <span className="font-medium" style={{ color: themeParams.text_color }}>{formatMoney(s.salary)} T</span>
                </div>
              ))}
              <div className="flex justify-between items-center pt-3 mt-2 font-bold" style={{ color: themeParams.button_color }}>
                <span>Итого:</span>
                <span>{formatMoney(salaryTotal)} T</span>
              </div>
            </div>
          )}

          {/* Shift data */}
          {shiftDataSubmitted && (
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <h3 className="text-lg font-semibold mb-3" style={{ color: themeParams.text_color }}>Данные смены</h3>
              {[
                ['Wolt', shiftData.wolt],
                ['Halyk', shiftData.halyk],
                ['Бумажные', shiftData.cashBills],
                ['Мелочь', shiftData.cashCoins],
                ['Расходы', shiftData.expenses],
              ].map(([label, val]) => (
                <div key={label} className="flex justify-between items-center py-2">
                  <span style={{ color: themeParams.hint_color }}>{label}</span>
                  <span className="font-medium" style={{ color: themeParams.text_color }}>{formatMoney(parseInputValue(val as string))} T</span>
                </div>
              ))}
            </div>
          )}

          {!shiftDataSubmitted && salariesCreated && (
            <div className="text-center py-8" style={{ color: themeParams.hint_color }}>
              Ожидание данных смены от кассира...
            </div>
          )}
        </div>
      </div>
    )
  }

  // ========================
  // Step Indicator
  // ========================
  const StepIndicator = () => (
    <div className="flex items-center justify-center gap-2 mb-6">
      {[1, 2, 3, 4].map(s => (
        <div key={s} className="flex items-center gap-2">
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium"
            style={{
              backgroundColor: s <= step ? themeParams.button_color : (themeParams.secondary_bg_color || '#e5e7eb'),
              color: s <= step ? themeParams.button_text_color : themeParams.hint_color,
            }}
          >
            {s < step ? '\u2713' : s}
          </div>
          {s < 4 && (
            <div className="w-6 h-0.5" style={{ backgroundColor: s < step ? themeParams.button_color : (themeParams.secondary_bg_color || '#e5e7eb') }} />
          )}
        </div>
      ))}
    </div>
  )

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen pb-8">
      <Header title="Закрытие смены — Кассир" showBack />

      <div className="p-4">
        <StepIndicator />

        {error && (
          <div className="mb-4 p-3 rounded-lg text-center" style={{ backgroundColor: '#fee2e2', color: '#ef4444' }}>
            {error}
          </div>
        )}

        {/* ======================== */}
        {/* STEP 1: Salary Input */}
        {/* ======================== */}
        {step === 1 && (
          <div className="space-y-4">
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <h3 className="text-lg font-semibold mb-4" style={{ color: themeParams.text_color }}>
                Зарплаты
              </h3>

              {/* Cashier count toggle */}
              <div className="mb-4">
                <label className="block text-sm mb-2" style={{ color: themeParams.hint_color }}>
                  Количество кассиров
                </label>
                <div className="flex gap-2">
                  {([2, 3] as const).map(n => (
                    <button
                      key={n}
                      onClick={() => setCashierCount(n)}
                      className="flex-1 py-3 rounded-lg font-medium text-lg"
                      style={{
                        backgroundColor: salaryInput.cashierCount === n ? themeParams.button_color : themeParams.bg_color,
                        color: salaryInput.cashierCount === n ? themeParams.button_text_color : themeParams.text_color,
                        border: `1px solid ${themeParams.hint_color}`,
                      }}
                    >
                      {n} кассира
                    </button>
                  ))}
                </div>
              </div>

              {/* Cashier names */}
              <div className="space-y-3 mb-4">
                {salaryInput.cashierNames.map((name, i) => (
                  <div key={i}>
                    <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                      Кассир {i + 1}
                    </label>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setCashierName(i, e.target.value)}
                      placeholder="Имя"
                      className="w-full p-3 rounded-lg border"
                      style={inputStyle}
                    />
                  </div>
                ))}
              </div>

              {/* Separator */}
              <div className="border-t my-4" style={{ borderColor: themeParams.hint_color }} />

              {/* Assistant start time */}
              <div className="mb-4">
                <label className="block text-sm mb-2" style={{ color: themeParams.hint_color }}>
                  Помощник вышел в
                </label>
                <div className="flex gap-2">
                  {(['10:00', '12:00', '14:00'] as const).map(t => (
                    <button
                      key={t}
                      onClick={() => setSalaryInput(prev => ({ ...prev, assistantStartTime: t }))}
                      className="flex-1 py-3 rounded-lg font-medium"
                      style={{
                        backgroundColor: salaryInput.assistantStartTime === t ? themeParams.button_color : themeParams.bg_color,
                        color: salaryInput.assistantStartTime === t ? themeParams.button_text_color : themeParams.text_color,
                        border: `1px solid ${themeParams.hint_color}`,
                      }}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>

              {/* Doner name */}
              <div className="mb-3">
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Донерщик
                </label>
                <input
                  type="text"
                  value={salaryInput.donerName}
                  onChange={(e) => setSalaryInput(prev => ({ ...prev, donerName: e.target.value }))}
                  placeholder="Имя"
                  className="w-full p-3 rounded-lg border"
                  style={inputStyle}
                />
              </div>

              {/* Assistant name */}
              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Помощник
                </label>
                <input
                  type="text"
                  value={salaryInput.assistantName}
                  onChange={(e) => setSalaryInput(prev => ({ ...prev, assistantName: e.target.value }))}
                  placeholder="Имя"
                  className="w-full p-3 rounded-lg border"
                  style={inputStyle}
                />
              </div>
            </div>

            {/* Next button */}
            <button
              onClick={handleCalculateSalaries}
              disabled={submitting}
              className="w-full p-4 rounded-lg font-medium text-lg"
              style={{
                backgroundColor: themeParams.button_color,
                color: themeParams.button_text_color,
                opacity: submitting ? 0.6 : 1,
              }}
            >
              {submitting ? 'Расчёт...' : 'Далее'}
            </button>
          </div>
        )}

        {/* ======================== */}
        {/* STEP 2: Salary Confirmation */}
        {/* ======================== */}
        {step === 2 && (
          <div className="space-y-4">
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <h3 className="text-lg font-semibold mb-4" style={{ color: themeParams.text_color }}>
                Подтверждение зарплат
              </h3>

              <div className="space-y-3">
                {confirmedSalaries.map((s, i) => (
                  <div
                    key={i}
                    className="flex justify-between items-center p-3 rounded-lg"
                    style={{ backgroundColor: themeParams.bg_color }}
                  >
                    <div>
                      <span className="font-medium" style={{ color: themeParams.text_color }}>{s.name}</span>
                      <span className="ml-2 text-sm" style={{ color: themeParams.hint_color }}>({s.role})</span>
                    </div>
                    <span className="font-bold text-lg" style={{ color: themeParams.text_color }}>
                      {formatMoney(s.salary)} T
                    </span>
                  </div>
                ))}
              </div>

              {/* Total */}
              <div
                className="mt-4 p-4 rounded-lg flex justify-between items-center"
                style={{ backgroundColor: themeParams.button_color }}
              >
                <span className="font-medium text-lg" style={{ color: themeParams.button_text_color }}>
                  Итого:
                </span>
                <span className="font-bold text-2xl" style={{ color: themeParams.button_text_color }}>
                  {formatMoney(salaryTotal)} T
                </span>
              </div>
            </div>

            {/* Buttons */}
            <div className="flex gap-3">
              <button
                onClick={() => setStep(1)}
                className="flex-1 p-4 rounded-lg font-medium"
                style={{
                  backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
                  color: themeParams.text_color,
                }}
              >
                Назад
              </button>
              <button
                onClick={handleConfirmSalaries}
                disabled={submitting}
                className="flex-1 p-4 rounded-lg font-medium"
                style={{
                  backgroundColor: '#22c55e',
                  color: '#ffffff',
                  opacity: submitting ? 0.6 : 1,
                }}
              >
                {submitting ? 'Создание...' : 'Подтвердить'}
              </button>
            </div>
          </div>
        )}

        {/* ======================== */}
        {/* STEP 3: Shift Data Input */}
        {/* ======================== */}
        {step === 3 && (
          <div className="space-y-4">
            {/* Salary summary (collapsed) */}
            {confirmedSalaries.length > 0 && (
              <div className="p-3 rounded-lg flex justify-between items-center" style={{ backgroundColor: '#dcfce7' }}>
                <span style={{ color: '#16a34a' }}>Зарплаты созданы</span>
                <span className="font-bold" style={{ color: '#16a34a' }}>{formatMoney(salaryTotal)} T</span>
              </div>
            )}

            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <h3 className="text-lg font-semibold mb-4" style={{ color: themeParams.text_color }}>
                Данные смены
              </h3>

              {/* Безнал */}
              <div className="space-y-3 mb-4">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Wolt</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={shiftData.wolt}
                      onChange={(e) => setShiftField('wolt', e.target.value)}
                      placeholder="0"
                      className="w-full p-3 rounded-lg border text-right"
                      style={inputStyle}
                    />
                  </div>
                  <div>
                    <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Halyk</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={shiftData.halyk}
                      onChange={(e) => setShiftField('halyk', e.target.value)}
                      placeholder="0"
                      className="w-full p-3 rounded-lg border text-right"
                      style={inputStyle}
                    />
                  </div>
                </div>
              </div>

              {/* Наличка */}
              <div className="space-y-3 mb-4 pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Бумажные</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={shiftData.cashBills}
                      onChange={(e) => setShiftField('cashBills', e.target.value)}
                      placeholder="0"
                      className="w-full p-3 rounded-lg border text-right"
                      style={inputStyle}
                    />
                  </div>
                  <div>
                    <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Мелочь</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={shiftData.cashCoins}
                      onChange={(e) => setShiftField('cashCoins', e.target.value)}
                      placeholder="0"
                      className="w-full p-3 rounded-lg border text-right"
                      style={inputStyle}
                    />
                  </div>
                </div>
              </div>

              {/* Расходы */}
              <div className="pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>Расходы</label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={shiftData.expenses}
                  onChange={(e) => setShiftField('expenses', e.target.value)}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={inputStyle}
                />
              </div>
            </div>

            {/* Submit button */}
            <button
              onClick={handleSubmitShiftData}
              disabled={submitting}
              className="w-full p-4 rounded-lg font-medium text-lg"
              style={{
                backgroundColor: themeParams.button_color,
                color: themeParams.button_text_color,
                opacity: submitting ? 0.6 : 1,
              }}
            >
              {submitting ? 'Отправка...' : 'Отправить'}
            </button>
          </div>
        )}

        {/* ======================== */}
        {/* STEP 4: Done */}
        {/* ======================== */}
        {step === 4 && (
          <div className="space-y-4">
            {/* Success message */}
            <div className="text-center py-8">
              <div
                className="w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-4"
                style={{ backgroundColor: '#dcfce7' }}
              >
                <span className="text-4xl" style={{ color: '#22c55e' }}>{'\u2713'}</span>
              </div>
              <h3 className="text-xl font-bold mb-2" style={{ color: themeParams.text_color }}>
                Данные отправлены
              </h3>
              <p style={{ color: themeParams.hint_color }}>
                Владелец увидит ваши данные в закрытии смены
              </p>
            </div>

            {/* Summary: Salaries */}
            {confirmedSalaries.length > 0 && (
              <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
                <h4 className="font-semibold mb-3" style={{ color: themeParams.text_color }}>Зарплаты</h4>
                {confirmedSalaries.map((s, i) => (
                  <div key={i} className="flex justify-between py-1.5">
                    <span style={{ color: themeParams.text_color }}>{s.name} <span className="text-sm" style={{ color: themeParams.hint_color }}>({s.role})</span></span>
                    <span className="font-medium" style={{ color: themeParams.text_color }}>{formatMoney(s.salary)} T</span>
                  </div>
                ))}
                <div className="flex justify-between pt-2 mt-2 border-t font-bold" style={{ borderColor: themeParams.hint_color, color: themeParams.button_color }}>
                  <span>Итого:</span>
                  <span>{formatMoney(salaryTotal)} T</span>
                </div>
              </div>
            )}

            {/* Summary: Shift data */}
            <div className="p-4 rounded-lg" style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}>
              <h4 className="font-semibold mb-3" style={{ color: themeParams.text_color }}>Данные смены</h4>
              {[
                ['Wolt', shiftData.wolt],
                ['Halyk', shiftData.halyk],
                ['Бумажные', shiftData.cashBills],
                ['Мелочь', shiftData.cashCoins],
                ['Расходы', shiftData.expenses],
              ].map(([label, val]) => (
                <div key={label} className="flex justify-between py-1.5">
                  <span style={{ color: themeParams.hint_color }}>{label}</span>
                  <span className="font-medium" style={{ color: themeParams.text_color }}>
                    {formatMoney(parseInputValue(val as string))} T
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

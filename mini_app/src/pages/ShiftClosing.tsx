import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'
import type { ShiftClosingCalculations } from '../types'

// Format number with spaces as thousands separator
function formatMoney(amount: number): string {
  return Math.round(amount).toLocaleString('ru-RU')
}

// Parse input value (allow expressions like 35270)
function parseInputValue(value: string): number {
  const cleaned = value.replace(/\s/g, '').replace(/,/g, '.')
  const num = parseFloat(cleaned)
  return isNaN(num) ? 0 : num
}

export const ShiftClosing: React.FC = () => {
  const navigate = useNavigate()
  const { webApp, themeParams } = useTelegram()

  // Load Poster data
  const { data: posterData, loading, error, refetch } = useApi(() =>
    getApiClient().getShiftClosingPosterData()
  )

  // Form state - manual input fields (all in tenge)
  const [wolt, setWolt] = useState('')
  const [halyk, setHalyk] = useState('')
  const [kaspi, setKaspi] = useState('')
  const [kaspiCafe, setKaspiCafe] = useState('')
  const [cashBills, setCashBills] = useState('')
  const [cashCoins, setCashCoins] = useState('')
  const [shiftStart, setShiftStart] = useState('')
  const [deposits, setDeposits] = useState('0')
  const [expenses, setExpenses] = useState('')
  const [cashToLeave, setCashToLeave] = useState('15000')

  // Calculated values
  const [calculations, setCalculations] = useState<ShiftClosingCalculations | null>(null)
  const [calculating, setCalculating] = useState(false)

  // Set shift_start from Poster data when loaded
  useEffect(() => {
    if (posterData?.shift_start) {
      setShiftStart(String(posterData.shift_start / 100))
    }
  }, [posterData])

  // Calculate totals when inputs change
  const calculateTotals = useCallback(async () => {
    if (!posterData) return

    setCalculating(true)

    try {
      const result = await getApiClient().calculateShiftClosing({
        wolt: parseInputValue(wolt),
        halyk: parseInputValue(halyk),
        kaspi: parseInputValue(kaspi),
        kaspi_cafe: parseInputValue(kaspiCafe),
        cash_bills: parseInputValue(cashBills),
        cash_coins: parseInputValue(cashCoins),
        shift_start: parseInputValue(shiftStart),
        deposits: parseInputValue(deposits),
        expenses: parseInputValue(expenses),
        cash_to_leave: parseInputValue(cashToLeave),
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
  }, [wolt, halyk, kaspi, kaspiCafe, cashBills, cashCoins, shiftStart, deposits, expenses, cashToLeave, posterData])

  // Debounced calculation
  useEffect(() => {
    const timer = setTimeout(() => {
      calculateTotals()
    }, 300)

    return () => clearTimeout(timer)
  }, [calculateTotals])

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

  const today = new Date().toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric'
  })

  // Day result styling
  const dayResult = calculations?.day_result ?? 0
  const isPositive = dayResult > 0
  const isNegative = dayResult < 0
  const dayResultColor = isPositive ? '#22c55e' : isNegative ? '#ef4444' : themeParams.text_color
  const dayResultLabel = isPositive ? '(излишек)' : isNegative ? '(недостача)' : '(идеально!)'

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen pb-8">
      <Header title="Закрытие смены" showBack />

      <div className="p-4">
        {/* Date Header */}
        <div
          className="text-center mb-6 p-3 rounded-lg"
          style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
        >
          <span style={{ color: themeParams.text_color }} className="text-lg font-semibold">
            {today}
          </span>
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
                    value={wolt}
                    onChange={(e) => setWolt(e.target.value)}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={{
                      backgroundColor: themeParams.bg_color || '#ffffff',
                      color: themeParams.text_color,
                      borderColor: themeParams.hint_color,
                    }}
                  />
                </div>
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                    Halyk
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={halyk}
                    onChange={(e) => setHalyk(e.target.value)}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={{
                      backgroundColor: themeParams.bg_color || '#ffffff',
                      color: themeParams.text_color,
                      borderColor: themeParams.hint_color,
                    }}
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
                  value={kaspi}
                  onChange={(e) => setKaspi(e.target.value)}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={{
                    backgroundColor: themeParams.bg_color || '#ffffff',
                    color: themeParams.text_color,
                    borderColor: themeParams.hint_color,
                  }}
                />
              </div>

              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  − Kaspi от PizzBurg-Cafe
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={kaspiCafe}
                  onChange={(e) => setKaspiCafe(e.target.value)}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={{
                    backgroundColor: themeParams.bg_color || '#ffffff',
                    color: themeParams.text_color,
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
                    value={cashBills}
                    onChange={(e) => setCashBills(e.target.value)}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={{
                      backgroundColor: themeParams.bg_color || '#ffffff',
                      color: themeParams.text_color,
                      borderColor: themeParams.hint_color,
                    }}
                  />
                </div>
                <div>
                  <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                    Наличка (мелочь)
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={cashCoins}
                    onChange={(e) => setCashCoins(e.target.value)}
                    placeholder="0"
                    className="w-full p-3 rounded-lg border text-right"
                    style={{
                      backgroundColor: themeParams.bg_color || '#ffffff',
                      color: themeParams.text_color,
                      borderColor: themeParams.hint_color,
                    }}
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
                    {formatMoney(calculations.fact_total)} ₸
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
                  value={shiftStart}
                  onChange={(e) => setShiftStart(e.target.value)}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={{
                    backgroundColor: themeParams.bg_color || '#ffffff',
                    color: themeParams.text_color,
                    borderColor: themeParams.hint_color,
                  }}
                />
              </div>

              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Внесение
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={deposits}
                  onChange={(e) => setDeposits(e.target.value)}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={{
                    backgroundColor: themeParams.bg_color || '#ffffff',
                    color: themeParams.text_color,
                    borderColor: themeParams.hint_color,
                  }}
                />
              </div>

              <div>
                <label className="block text-sm mb-1" style={{ color: themeParams.hint_color }}>
                  Расход с кассы
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={expenses}
                  onChange={(e) => setExpenses(e.target.value)}
                  placeholder="0"
                  className="w-full p-3 rounded-lg border text-right"
                  style={{
                    backgroundColor: themeParams.bg_color || '#ffffff',
                    color: themeParams.text_color,
                    borderColor: themeParams.hint_color,
                  }}
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
                    {formatMoney(calculations.fact_adjusted)} ₸
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
                  {calculations ? formatMoney(calculations.fact_cashless) : '—'} ₸
                </span>
              </div>

              <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                <span style={{ color: themeParams.hint_color }}>Картой (Poster):</span>
                <span style={{ color: themeParams.text_color }} className="font-medium">
                  {posterData ? formatMoney(posterData.poster_card / 100) : '—'} ₸
                </span>
              </div>

              {calculations && Math.abs(calculations.cashless_diff) >= 1 && (
                <div
                  className="flex justify-between items-center p-2 rounded"
                  style={{ backgroundColor: calculations.cashless_diff > 0 ? '#dcfce7' : '#fee2e2' }}
                >
                  <span style={{ color: themeParams.hint_color }}>Разница безнал:</span>
                  <span style={{ color: calculations.cashless_diff > 0 ? '#22c55e' : '#ef4444' }} className="font-medium">
                    {calculations.cashless_diff > 0 ? '+' : ''}{formatMoney(calculations.cashless_diff)} ₸
                  </span>
                </div>
              )}
            </div>

            {/* Poster API Data */}
            <div className="space-y-3 pt-3 border-t" style={{ borderColor: themeParams.hint_color }}>
              <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                <span style={{ color: themeParams.hint_color }}>Poster (торговля):</span>
                <span style={{ color: themeParams.text_color }} className="font-medium">
                  {posterData ? formatMoney(posterData.trade_total / 100) : '—'} ₸
                </span>
              </div>

              <div className="flex justify-between items-center p-2 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                <span style={{ color: themeParams.hint_color }}>Бонусы:</span>
                <span style={{ color: '#ef4444' }} className="font-medium">
                  −{posterData ? formatMoney(posterData.bonus / 100) : '—'} ₸
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
                  {calculations ? formatMoney(calculations.poster_total) : '—'} ₸
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
              {calculations ? (dayResult > 0 ? '+' : '') + formatMoney(dayResult) : '—'} ₸
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
                  value={cashToLeave}
                  onChange={(e) => setCashToLeave(e.target.value)}
                  placeholder="15000"
                  className="w-full p-3 rounded-lg border text-right"
                  style={{
                    backgroundColor: themeParams.bg_color || '#ffffff',
                    color: themeParams.text_color,
                    borderColor: themeParams.hint_color,
                  }}
                />
              </div>

              {calculations && (
                <>
                  <div className="flex justify-between items-center p-3 rounded" style={{ backgroundColor: themeParams.bg_color }}>
                    <span style={{ color: themeParams.text_color }}>
                      Смена оставили:
                    </span>
                    <span style={{ color: themeParams.text_color }} className="font-bold">
                      {formatMoney(calculations.shift_left)} ₸
                    </span>
                  </div>
                  <div className="text-xs text-center" style={{ color: themeParams.hint_color }}>
                    ({formatMoney(parseInputValue(cashToLeave))} бумажные + {formatMoney(parseInputValue(cashCoins))} мелочь)
                  </div>

                  <div
                    className="flex justify-between items-center p-4 rounded-lg"
                    style={{ backgroundColor: themeParams.button_color }}
                  >
                    <span style={{ color: themeParams.button_text_color }} className="font-medium text-lg">
                      Инкассация:
                    </span>
                    <span style={{ color: themeParams.button_text_color }} className="text-2xl font-bold">
                      {formatMoney(calculations.collection)} ₸
                    </span>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Transactions count info */}
          {posterData && (
            <div className="text-center text-sm" style={{ color: themeParams.hint_color }}>
              Закрытых заказов: {posterData.transactions_count}
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

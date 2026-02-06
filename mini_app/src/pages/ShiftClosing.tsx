import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTelegram } from '../hooks/useTelegram'
import { useApi } from '../hooks/useApi'
import { getApiClient } from '../api/client'
import { Header } from '../components/Header'
import { Loading } from '../components/Loading'
import { ErrorMessage } from '../components/ErrorMessage'

// Format number with spaces as thousands separator
function formatMoney(amount: number): string {
  return Math.round(amount).toLocaleString('ru-RU')
}

// Parse input value
function parseInputValue(value: string): number {
  const cleaned = value.replace(/\s/g, '').replace(/,/g, '.')
  const num = parseFloat(cleaned)
  return isNaN(num) ? 0 : num
}

interface ReconciliationRow {
  label: string
  expected: number
  fact: string
  setFact: (value: string) => void
}

export const ShiftClosing: React.FC = () => {
  const navigate = useNavigate()
  const { webApp, themeParams } = useTelegram()

  // Load Poster data
  const { data: posterData, loading, error, refetch } = useApi(() =>
    getApiClient().getShiftClosingPosterData()
  )

  // Fact inputs
  const [kaspiFact, setKaspiFact] = useState('')
  const [halykFact, setHalykFact] = useState('')
  const [cashFact, setCashFact] = useState('')

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

  // Calculate differences
  const kaspiExpected = posterData?.kaspi_expected ?? 0
  const halykExpected = posterData?.halyk_expected ?? 0
  const cashExpected = posterData?.cash_expected ?? 0

  const kaspiDiff = parseInputValue(kaspiFact) - kaspiExpected
  const halykDiff = parseInputValue(halykFact) - halykExpected
  const cashDiff = parseInputValue(cashFact) - cashExpected

  // Rows config
  const rows: ReconciliationRow[] = [
    { label: 'Kaspi Pay', expected: kaspiExpected, fact: kaspiFact, setFact: setKaspiFact },
    { label: 'Halyk', expected: halykExpected, fact: halykFact, setFact: setHalykFact },
    { label: 'Наличка', expected: cashExpected, fact: cashFact, setFact: setCashFact },
  ]

  const diffs = [kaspiDiff, halykDiff, cashDiff]

  // Render difference with color
  const renderDiff = (diff: number, hasFact: boolean) => {
    if (!hasFact) return <span style={{ color: themeParams.hint_color }}>—</span>

    const isPositive = diff > 0
    const isNegative = diff < 0
    const color = isPositive ? '#22c55e' : isNegative ? '#ef4444' : themeParams.text_color
    const prefix = isPositive ? '+' : ''

    return (
      <span style={{ color, fontWeight: 600 }}>
        {prefix}{formatMoney(diff)} ₸
      </span>
    )
  }

  return (
    <div style={{ backgroundColor: themeParams.bg_color || '#ffffff' }} className="min-h-screen pb-8">
      <Header title="Сверка смены" showBack />

      <div className="p-4">
        {/* Date Header */}
        <div
          className="text-center mb-6 p-3 rounded-lg"
          style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
        >
          <span style={{ color: themeParams.text_color }} className="text-lg font-semibold">
            {today}
          </span>
          {posterData?.accounts_count && (
            <span style={{ color: themeParams.hint_color }} className="text-sm ml-2">
              ({posterData.accounts_count} акк.)
            </span>
          )}
        </div>

        {/* Reconciliation Table */}
        <div
          className="rounded-lg overflow-hidden"
          style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
        >
          {/* Header */}
          <div
            className="grid grid-cols-3 gap-2 p-3 text-sm font-medium"
            style={{ borderBottom: `1px solid ${themeParams.hint_color}40` }}
          >
            <div style={{ color: themeParams.hint_color }}>Poster</div>
            <div style={{ color: themeParams.hint_color }}>Факт (ввод)</div>
            <div style={{ color: themeParams.hint_color }}>Разница</div>
          </div>

          {/* Rows */}
          {rows.map((row, idx) => (
            <div
              key={row.label}
              className="p-3"
              style={{
                borderBottom: idx < rows.length - 1 ? `1px solid ${themeParams.hint_color}20` : undefined
              }}
            >
              {/* Row label */}
              <div
                className="text-sm font-medium mb-2"
                style={{ color: themeParams.text_color }}
              >
                {row.label}
              </div>

              {/* Values grid */}
              <div className="grid grid-cols-3 gap-2 items-center">
                {/* Expected from Poster */}
                <div
                  className="text-right font-medium"
                  style={{ color: themeParams.text_color }}
                >
                  {formatMoney(row.expected)} ₸
                </div>

                {/* Fact input */}
                <input
                  type="text"
                  inputMode="numeric"
                  value={row.fact}
                  onChange={(e) => row.setFact(e.target.value)}
                  placeholder="0"
                  className="w-full p-2 rounded-lg border text-right"
                  style={{
                    backgroundColor: themeParams.bg_color || '#ffffff',
                    color: themeParams.text_color,
                    borderColor: themeParams.hint_color,
                  }}
                />

                {/* Difference */}
                <div className="text-right">
                  {renderDiff(diffs[idx], row.fact !== '')}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Legend */}
        <div
          className="mt-4 p-3 rounded-lg text-sm"
          style={{ backgroundColor: themeParams.secondary_bg_color || '#f3f4f6' }}
        >
          <div style={{ color: themeParams.hint_color }} className="mb-2">
            Разница = Факт − Poster
          </div>
          <div className="flex gap-4">
            <span>
              <span style={{ color: '#22c55e' }}>●</span> Излишек (+)
            </span>
            <span>
              <span style={{ color: '#ef4444' }}>●</span> Недостача (−)
            </span>
          </div>
        </div>

        {/* Total Orders Info */}
        {posterData && (
          <div className="text-center text-sm mt-4" style={{ color: themeParams.hint_color }}>
            Закрытых заказов: {posterData.transactions_count}
          </div>
        )}
      </div>
    </div>
  )
}

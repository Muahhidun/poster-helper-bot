import React from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import { useTelegram } from '../hooks/useTelegram'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
)

interface ChartProps {
  data: Array<{ date: string; accuracy: number }>
}

export const Chart: React.FC<ChartProps> = ({ data }) => {
  const { themeParams } = useTelegram()

  const chartData = {
    labels: data.map((d) => {
      const date = new Date(d.date)
      return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
    }),
    datasets: [
      {
        label: 'Точность (%)',
        data: data.map((d) => d.accuracy),
        borderColor: themeParams.link_color || '#3b82f6',
        backgroundColor: `${themeParams.link_color || '#3b82f6'}20`,
        fill: true,
        tension: 0.4,
      },
    ],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: false,
      },
      tooltip: {
        backgroundColor: themeParams.secondary_bg_color || '#f3f4f6',
        titleColor: themeParams.text_color || '#000000',
        bodyColor: themeParams.text_color || '#000000',
        borderColor: themeParams.hint_color || '#e5e7eb',
        borderWidth: 1,
      },
    },
    scales: {
      y: {
        min: 0,
        max: 100,
        ticks: {
          color: themeParams.hint_color || '#6b7280',
        },
        grid: {
          color: `${themeParams.hint_color || '#e5e7eb'}40`,
        },
      },
      x: {
        ticks: {
          color: themeParams.hint_color || '#6b7280',
        },
        grid: {
          display: false,
        },
      },
    },
  }

  return (
    <div className="h-48">
      <Line data={chartData} options={options} />
    </div>
  )
}

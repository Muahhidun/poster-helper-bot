// Format number with spaces as thousands separator
export function formatMoney(amount: number): string {
  return Math.round(amount).toLocaleString('ru-RU')
}

// Parse input value: handle spaces, commas, etc.
export function parseInputValue(value: string): number {
  const cleaned = value.replace(/\s/g, '').replace(/,/g, '.')
  const num = parseFloat(cleaned)
  return isNaN(num) ? 0 : num
}

// Get today's date in Kazakhstan timezone (UTC+5 Asia/Almaty) as YYYY-MM-DD
export function getKzToday(): string {
  const now = new Date()
  const kzTime = new Date(now.getTime() + 5 * 3600000)
  return kzTime.toISOString().slice(0, 10)
}

// Format YYYY-MM-DD to DD.MM.YYYY
export function formatDate(dateStr: string): string {
  const [y, m, d] = dateStr.split('-')
  return `${d}.${m}.${y}`
}

// Format YYYY-MM-DD to YYYYMMDD (for Poster API)
export function toPosterDate(dateStr: string): string {
  return dateStr.replace(/-/g, '')
}

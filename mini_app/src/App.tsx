import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useTelegram } from './hooks/useTelegram'
import { initApiClient } from './api/client'
import { AppLayout } from './layouts/AppLayout'

// Pages
import { Expenses } from './pages/Expenses'
import { SupplyDrafts } from './pages/SupplyDrafts'
import { CreateSupply } from './pages/CreateSupply'
import { Aliases } from './pages/Aliases'
import { AliasForm } from './pages/AliasForm'
import { Templates } from './pages/Templates'
import { ShiftClosing } from './pages/ShiftClosing'
import { Loading } from './components/Loading'

// Create a client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      retry: 1,
    },
  },
})

function AppContent() {
  const { webApp, isReady } = useTelegram()

  useEffect(() => {
    // Initialize API client with Telegram initData (or empty string for development)
    const initData = webApp?.initData || ''
    initApiClient(initData)

    if (webApp) {
      console.log('Telegram WebApp initialized:', {
        version: webApp.version,
        platform: webApp.platform,
        user: webApp.initDataUnsafe?.user,
      })
      // Expand the app to full height
      webApp.expand()
    } else {
      console.warn('Running in development mode without Telegram WebApp')
    }
  }, [webApp])

  if (!isReady) {
    return <Loading />
  }

  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<Expenses />} />
        <Route path="/expenses" element={<Expenses />} />
        <Route path="/supplies" element={<SupplyDrafts />} />
        <Route path="/supplies/new" element={<CreateSupply />} />
        <Route path="/aliases" element={<Aliases />} />
        <Route path="/aliases/new" element={<AliasForm />} />
        <Route path="/aliases/:id/edit" element={<AliasForm />} />
        <Route path="/templates" element={<Templates />} />
        <Route path="/shift-closing" element={<ShiftClosing />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/mini-app">
        <AppContent />
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App

import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useTelegram } from './hooks/useTelegram'
import { initApiClient } from './api/client'
import { Dashboard } from './pages/Dashboard'
import { SupplyHistory } from './pages/SupplyHistory'
import { SupplyDetail } from './pages/SupplyDetail'
import { Aliases } from './pages/Aliases'
import { AliasForm } from './pages/AliasForm'
import { Loading } from './components/Loading'

function App() {
  const { webApp, isReady } = useTelegram()

  useEffect(() => {
    if (webApp) {
      // Initialize API client with Telegram initData
      initApiClient(webApp.initData)
    }
  }, [webApp])

  if (!isReady) {
    return <Loading />
  }

  return (
    <BrowserRouter basename="/mini-app">
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/supplies" element={<SupplyHistory />} />
        <Route path="/supplies/:id" element={<SupplyDetail />} />
        <Route path="/aliases" element={<Aliases />} />
        <Route path="/aliases/new" element={<AliasForm />} />
        <Route path="/aliases/:id/edit" element={<AliasForm />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App

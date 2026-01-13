import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useTelegram } from './hooks/useTelegram'
import { initApiClient } from './api/client'
import { Dashboard } from './pages/Dashboard'
import { SupplyHistory } from './pages/SupplyHistory'
import { SupplyDetail } from './pages/SupplyDetail'
import { CreateSupply } from './pages/CreateSupply'
import { Aliases } from './pages/Aliases'
import { AliasForm } from './pages/AliasForm'
import { Templates } from './pages/Templates'
import { Loading } from './components/Loading'

function App() {
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
    } else {
      console.warn('Running in development mode without Telegram WebApp')
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
        <Route path="/supplies/new" element={<CreateSupply />} />
        <Route path="/supplies/:id" element={<SupplyDetail />} />
        <Route path="/aliases" element={<Aliases />} />
        <Route path="/aliases/new" element={<AliasForm />} />
        <Route path="/aliases/:id/edit" element={<AliasForm />} />
        <Route path="/templates" element={<Templates />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App

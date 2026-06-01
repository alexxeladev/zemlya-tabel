import { useEffect } from 'react'
import { useAuthStore } from './store/auth'
import { AppRouter } from './routes/AppRouter'

function App() {
  const loadUserFromToken = useAuthStore((s) => s.loadUserFromToken)

  useEffect(() => {
    void loadUserFromToken()
  }, [loadUserFromToken])

  return <AppRouter />
}

export default App

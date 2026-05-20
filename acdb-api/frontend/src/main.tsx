import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './i18n'
import App from './App'

console.info(`[CC] Frontend version: ${__APP_VERSION__} (built ${__BUILD_TIME__})`)
fetch('/health')
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
  .then((h) => {
    console.info('[CC] Backend health:', h)
    if (h?.version) {
      console.info(`[CC] Backend version: ${h.version}`)
    }
  })
  .catch((err) => {
    console.warn('[CC] Backend health unavailable:', err)
  })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/inter'
import '@fontsource-variable/space-grotesk'
import '@fontsource-variable/jetbrains-mono'
import './styles/tokens.css'
import './index.css'
import { DEMO_MODE, applyTheme } from './lib/runtime'

applyTheme()

const start = async () => {
  if (DEMO_MODE) {
    // Loaded only in demo mode so the live bundle never carries the sample data.
    const { installDemoTransport } = await import('./demo/transport')
    installDemoTransport()
  } else {
    // Live mode: attach the API's per-launch token to every write.
    const { installApiAuth } = await import('./lib/apiAuth')
    installApiAuth()
  }
  const { default: App } = await import('./App.jsx')
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void start()

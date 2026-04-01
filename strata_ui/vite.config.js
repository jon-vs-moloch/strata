import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const tauriConfig = JSON.parse(readFileSync(resolve(__dirname, '..', 'src-tauri', 'tauri.conf.json'), 'utf8'))
const appVersion = process.env.STRATA_DESKTOP_BUILD_VERSION || tauriConfig.version || '0.0.0'
const appChannel = process.env.STRATA_DESKTOP_UPDATE_CHANNEL || 'dev'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    __APP_CHANNEL__: JSON.stringify(appChannel),
  },
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  server: {
    port: 5173,
    proxy: {
      '/pack': 'http://127.0.0.1:8000',
      '/shipping': 'http://127.0.0.1:8000',
    },
  },
})

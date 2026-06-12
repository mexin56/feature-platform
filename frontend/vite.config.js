import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': 'http://localhost:8100',
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1500 },
})

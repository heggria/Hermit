import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api/ws': {
        target: 'http://localhost:8323',
        changeOrigin: true,
        ws: true,
      },
      '/api': {
        target: 'http://localhost:8323',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})

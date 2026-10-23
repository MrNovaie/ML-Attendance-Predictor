import { defineConfig, mergeConfig } from 'vite'
import base from './vite.config.js'

// Demo only: one public address serves both the page and the API, so the login cookie stays same-origin.
export default mergeConfig(
  base,
  defineConfig({
    server: {
      allowedHosts: ['.trycloudflare.com'],
      proxy: { '/api': 'http://localhost:5000' },
    },
  }),
)
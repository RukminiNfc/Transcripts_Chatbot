import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // Installable PWA (phones/tablets "Add to Home Screen", desktop "Install app").
    VitePWA({
      // New builds activate on the next page load without a prompt.
      registerType: 'autoUpdate',
      includeAssets: ['nfclogo.jpg', 'apple-touch-icon.png'],
      manifest: {
        name: 'NFC Transcript Chatbot',
        short_name: 'Transcripts',
        description: 'Ask questions about meetings, minutes and requirements.',
        theme_color: '#1F2A44',
        background_color: '#F7F6F2',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
          // Icons were rendered with an 80% safe zone, so the 512 doubles as maskable.
          { src: 'pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Cache only the built app shell. API traffic is never cached: answers, minutes and
        // auth must always be live, and a cached 401/200 would be worse than a network error.
        globPatterns: ['**/*.{js,css,html,png,jpg,svg,woff2}'],
        // SheetJS (~500 KB) loads only when someone exports to Excel — don't precache it.
        globIgnores: ['**/xlsx-*.js'],
        navigateFallback: '/index.html',
        runtimeCaching: [],
      },
    }),
  ],
})

import process from 'node:process'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react-swc'
import { VitePWA } from 'vite-plugin-pwa'

// Where the app is served from, read from frontend/.env (see .env.example):
//   local : "/"                    (default — nothing to set)
//   server: "/TranscriptionVal/"   (the app sits under that path behind the proxy)
// Always normalised to a leading and trailing "/". Vite exposes it to the code as
// import.meta.env.BASE_URL, which services/api.js turns into the router basename, the
// login redirect and the logo paths — so no path is ever hard-coded per machine.
const normaliseBase = (raw) => `/${(raw || '').trim().replace(/^\/+|\/+$/g, '')}/`.replace(/\/{2,}/g, '/')

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const base = normaliseBase(env.VITE_BASE_PATH)

  return {
    base,
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
          // Both follow the deployment base, or the installed app opens outside it on the server.
          start_url: base,
          scope: base,
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
          navigateFallback: `${base}index.html`,
          runtimeCaching: [],
        },
      }),
    ],
  }
})

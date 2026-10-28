import React from 'react'
import ReactDOM from 'react-dom/client'
import { ThemeProvider } from '@mui/material/styles'
import CssBaseline from '@mui/material/CssBaseline'
import App from './App'
import theme from './theme/theme'
import { AuthProvider } from './auth/AuthContext'
import { API_ORIGIN, redirectToLogin } from './services/api'

// --- Global fetch wrapper: attach the JWT to every fetch() call to our backend. ---
// Admin/Requirements pages use raw fetch(); this adds the token once, centrally,
// instead of editing each call. It also logs the user out on a 401 (expired token).
const _origFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  const token = localStorage.getItem('token');
  const hitsBackend = url.includes(API_ORIGIN);
  if (token && hitsBackend) {
    init = { ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` } };
  }
  return _origFetch(input, init).then((resp) => {
    if (resp.status === 401 && hitsBackend) {
      localStorage.removeItem('token');
      localStorage.removeItem('role');
      localStorage.removeItem('username');
      redirectToLogin();
    }
    return resp;
  });
};

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AuthProvider>
        <App />
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>
)

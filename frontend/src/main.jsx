import React from 'react'
import ReactDOM from 'react-dom/client'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import CssBaseline from '@mui/material/CssBaseline'
import App from './App'
import { AuthProvider } from './auth/AuthContext'

// --- Global fetch wrapper: attach the JWT to every fetch() call to our backend. ---
// Admin/Requirements pages use raw fetch(); this adds the token once, centrally,
// instead of editing each call. It also logs the user out on a 401 (expired token).
const _origFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  const token = localStorage.getItem('token');
  const hitsBackend = url.includes('localhost:8001');
  if (token && hitsBackend) {
    init = { ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` } };
  }
  return _origFetch(input, init).then((resp) => {
    if (resp.status === 401 && hitsBackend) {
      localStorage.removeItem('token');
      localStorage.removeItem('role');
      localStorage.removeItem('username');
      if (window.location.pathname !== '/login') window.location.href = '/login';
    }
    return resp;
  });
};

const theme = createTheme({
  palette: {
    primary: { main: '#0d76ff', contrastText: '#ffffff' },
    secondary: { main: '#ffffff', contrastText: '#0d76ff' },
    background: { default: '#ffffff', paper: '#ffffff' },
  },
})

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

import axios from 'axios';

const API_BASE_URL = 'http://localhost:8001/api';
// Some routers (e.g. /requirements) sit outside the /api prefix, so they need the bare origin.
export const API_ORIGIN = 'http://localhost:8001';

// --- token storage helpers (single source of truth: localStorage) ---
export const getToken = () => localStorage.getItem('token');

// The in-progress chat lives in sessionStorage (so it survives a refresh). It must be cleared on
// login AND logout, or the NEXT user on the same browser would see the PREVIOUS user's conversation.
const clearChatState = () => {
  sessionStorage.removeItem('chat_messages');
  sessionStorage.removeItem('chat_session_id');
};

export const setAuth = ({ access_token, role, username }) => {
  clearChatState();                       // fresh login -> never inherit a previous user's chat
  localStorage.setItem('token', access_token);
  localStorage.setItem('role', role);
  localStorage.setItem('username', username);
};
export const clearAuth = () => {
  localStorage.removeItem('token');
  localStorage.removeItem('role');
  localStorage.removeItem('username');
  clearChatState();                       // logout / token-expiry -> wipe the conversation too
};

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

// Attach the JWT to every axios request (if logged in).
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// On an expired/invalid token (401), log out and go to the login page.
api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error?.response?.status === 401) {
      clearAuth();
      if (window.location.pathname !== '/login') window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// --- Auth APIs ---
export const authAPI = {
  // Backend /login expects form-encoded fields (OAuth2PasswordRequestForm), not JSON.
  login: async (username, password) => {
    const form = new URLSearchParams();
    form.append('username', username);
    form.append('password', password);
    const resp = await axios.post(`${API_BASE_URL}/auth/login`, form, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    return resp.data; // { access_token, token_type, role, username }
  },
  me: async () => {
    const resp = await api.get('/auth/me');
    return resp.data; // { username, role }
  },
};

// Chat APIs
export const chatAPI = {
  sendMessage: async (query, sessionId = null) => {
    const response = await api.post('/chat/', { query, session_id: sessionId });
    return response.data;
  },

  // --- per-user chat history ---
  listSessions: async () => {
    const resp = await api.get('/chat/sessions');
    return resp.data; // [{session_id, title, last_activity, message_count}]
  },
  getSession: async (sessionId) => {
    const resp = await api.get(`/chat/sessions/${sessionId}`);
    return resp.data; // {session_id, messages: [{role, content, timestamp}]}
  },
  deleteSession: async (sessionId) => {
    const resp = await api.delete(`/chat/sessions/${sessionId}`);
    return resp.data;
  },
  renameSession: async (sessionId, title) => {
    const resp = await api.patch(`/chat/sessions/${sessionId}`, { title });
    return resp.data;
  },

  // Streaming chat over SSE. Sends the JWT so the (now protected) endpoint accepts it.
  sendMessageStream: async (query, sessionId, { onDelta, onDone, onError } = {}) => {
    try {
      const token = getToken();
      const resp = await fetch(`${API_BASE_URL}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ query, session_id: sessionId }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const events = buffer.split('\n\n');
        buffer = events.pop();
        for (const evt of events) {
          const line = evt.trim();
          if (!line.startsWith('data:')) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          let data;
          try { data = JSON.parse(payload); } catch { continue; }
          if (data.type === 'delta') onDelta?.(data.text);
          else if (data.type === 'done') onDone?.(data);
          else if (data.type === 'error') onError?.(new Error(data.message || 'stream error'));
        }
      }
    } catch (err) {
      onError?.(err);
    }
  },
};

// --- Minutes of Meeting (MOM) APIs ---
// Everything goes through `api`, so the JWT is attached automatically. The whole /api/mom router
// requires a login; generate + send additionally require admin (403 for normal users), which is
// why the UI hides those buttons rather than letting them fail.

// Mirrors the backend's safe_filename() (document_render.py) so the saved file is named the same
// way the server names it. We can't read the server's name: Content-Disposition is not in the
// CORS expose_headers list, so the browser hides it from JS. Kept as a fallback in case it is.
const momFilename = (sessionName) => {
  const stem = `MOM_${sessionName || 'minutes'}`
    .replace(/[^A-Za-z0-9._-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 120);
  return `${stem || 'document'}.docx`;
};

export const momAPI = {
  // Latest version per meeting (the backend collapses the history unless all_versions=true).
  list: async () => {
    const resp = await api.get('/mom/');
    return resp.data;
  },

  // One MOM including the full markdown body.
  get: async (momId) => {
    const resp = await api.get(`/mom/${momId}`);
    return resp.data;
  },

  // Admin only.
  generate: async (transcriptId) => {
    const resp = await api.post(`/mom/generate/${transcriptId}`);
    return resp.data;
  },

  // Admin only. Emails the project's subscriber list.
  send: async (momId) => {
    const resp = await api.post(`/mom/${momId}/send`);
    return resp.data;
  },

  // Meetings with no minutes yet. /requirements is admin-only AND outside the /api prefix, so
  // this takes the bare origin and callers must check isAdmin before calling it.
  listTranscripts: async () => {
    const resp = await api.get(`${API_ORIGIN}/requirements/transcripts`);
    return resp.data;
  },

  /**
   * Download the Word document and hand it to the browser.
   *
   * A plain <a href> cannot be used: the endpoint needs the Authorization header, and an anchor
   * sends none. So we fetch the bytes as a blob and click a temporary object-URL link instead.
   */
  download: async (momId, sessionName) => {
    let resp;
    try {
      resp = await api.get(`/mom/${momId}/download`, { responseType: 'blob' });
    } catch (err) {
      // With responseType 'blob', an error body arrives as a Blob too — so the usual
      // err.response.data.detail is not a string. Read it back before reporting.
      const data = err?.response?.data;
      let detail = '';
      if (data instanceof Blob) {
        try { detail = JSON.parse(await data.text())?.detail || ''; } catch { /* not JSON */ }
      }
      throw detail ? new Error(detail) : err;
    }

    const disposition = resp.headers?.['content-disposition'] || '';
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
    const filename = match ? decodeURIComponent(match[1]) : momFilename(sessionName);

    const url = window.URL.createObjectURL(resp.data);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoking immediately can cancel the download in some browsers; give it a moment.
    setTimeout(() => window.URL.revokeObjectURL(url), 1000);
    return filename;
  },
};

export default api;

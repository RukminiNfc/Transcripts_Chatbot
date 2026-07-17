import axios from 'axios';

const API_BASE_URL = 'http://localhost:8001/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Chat APIs
export const chatAPI = {
  sendMessage: async (query, sessionId = null) => {
    const response = await api.post('/chat/', {
      query,
      session_id: sessionId,
    });
    return response.data;
  },

  // Streaming chat over Server-Sent Events. Calls onDelta(text) as tokens arrive,
  // onDone(evt) with {session_id, sources, context_metadata}, and onError(err) on failure.
  sendMessageStream: async (query, sessionId, { onDelta, onDone, onError } = {}) => {
    try {
      const resp = await fetch(`${API_BASE_URL}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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

        // SSE events are separated by a blank line; keep any trailing partial event.
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

export default api;
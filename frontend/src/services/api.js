import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Metadata APIs - for dynamic dropdowns
export const metadataAPI = {
  getGuideTypes: async () => {
    const response = await api.get('/metadata/guide-types');
    return response.data;
  },

  getJurisdictions: async (guideType = null) => {
    const params = guideType ? { guide_type: guideType } : {};
    const response = await api.get('/metadata/jurisdictions', { params });
    return response.data;
  },

  getDocuments: async (guideType = null, jurisdiction = null) => {
    const params = {};
    if (guideType) params.guide_type = guideType;
    if (jurisdiction) params.jurisdiction = jurisdiction;
    const response = await api.get('/metadata/documents', { params });
    return response.data;
  },

  getHierarchy: async () => {
    const response = await api.get('/metadata/hierarchy');
    return response.data;
  },
};

// Document APIs
export const documentAPI = {
  upload: async (file, guideType, jurisdiction) => {
    const formData = new FormData();
    formData.append('file', file);
    if (guideType) formData.append('guide_type', guideType);
    if (jurisdiction) formData.append('jurisdiction', jurisdiction);

    const response = await axios.post(`${API_BASE_URL}/documents/upload`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  },

  list: async (filters = {}) => {
    const response = await api.get('/documents/', { params: filters });
    return response.data;
  },

  get: async (documentId) => {
    const response = await api.get(`/documents/${documentId}`);
    return response.data;
  },

  delete: async (documentId) => {
    const response = await api.delete(`/documents/${documentId}`);
    return response.data;
  },

  getDownloadUrl: (documentId) => {
    return `${API_BASE_URL}/documents/${documentId}/download`;
  },
};

// Search APIs
export const searchAPI = {
  search: async (query, filters = {}) => {
    const response = await api.post('/search/', {
      query,
      ...filters,
    });
    return response.data;
  },

  multiCountrySearch: async (query, guideType = null, topK = 3) => {
    const response = await api.post('/search/multi-country', {
      query,
      guide_type: guideType,
      top_k: topK,
    });
    return response.data;
  },
};

// Chat APIs
export const chatAPI = {
  sendMessage: async (query, sessionId = null, filters = null) => {
    const response = await api.post('/chat/', {
      query,
      session_id: sessionId,
      filters,
    });
    // Response now includes context_metadata alongside answer, sources, session_id
    return response.data;
  },

  lockDocument: async (sessionId, documentId) => {
    const response = await api.post(`/chat/lock-document/${sessionId}/${documentId}`);
    return response.data;
  },

  unlockDocument: async (sessionId) => {
    const response = await api.post(`/chat/unlock-document/${sessionId}`);
    return response.data;
  },
};

export default api;
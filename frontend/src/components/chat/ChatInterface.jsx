import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Paper, Typography, Button, Alert, TextField,
  List, ListItem, ListItemButton, ListItemText, IconButton, Divider, Tooltip,
} from '@mui/material';
import { Add, Delete, Edit } from '@mui/icons-material';
import { v4 as uuidv4 } from 'uuid';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import TypingIndicator from './TypingIndicator';
import { chatAPI } from '../../services/api';
import './ChatStyles.css';

function ChatInterface() {
  // Restore the in-progress conversation from sessionStorage so switching tabs / refreshing
  // does not lose it. The sidebar list, however, is the source of truth (from the backend).
  const [messages, setMessages] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem('chat_messages')) || []; }
    catch { return []; }
  });
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem('chat_session_id') || null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [inputExternalValue, setInputExternalValue] = useState('');
  const [sessions, setSessions] = useState([]); // this user's past chats
  const [editingId, setEditingId] = useState(null);   // session being renamed
  const [editingTitle, setEditingTitle] = useState('');

  useEffect(() => {
    if (!sessionId) setSessionId(uuidv4());
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    sessionStorage.setItem('chat_messages', JSON.stringify(messages));
  }, [messages]);

  useEffect(() => {
    if (sessionId) sessionStorage.setItem('chat_session_id', sessionId);
  }, [sessionId]);

  // Load THIS user's chat history for the sidebar.
  const loadSessions = useCallback(async () => {
    try {
      const data = await chatAPI.listSessions();
      setSessions(data || []);
    } catch {
      /* sidebar is non-critical; ignore load errors */
    }
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  const handleSendMessage = async (messageText) => {
    setError(null);
    setLoading(true);
    setMessages((prev) => [...prev, { role: 'user', content: messageText }]);

    const appendToAssistant = (text) => {
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === 'assistant' && last.streaming) {
          copy[copy.length - 1] = { ...last, content: last.content + text };
        } else {
          copy.push({ role: 'assistant', content: text, streaming: true });
        }
        return copy;
      });
    };

    try {
      await chatAPI.sendMessageStream(messageText, sessionId, {
        onDelta: (text) => appendToAssistant(text),
        onDone: (evt) => {
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.role === 'assistant' && last.streaming) {
              copy[copy.length - 1] = {
                ...last, streaming: false,
                sources: evt.sources || [],
                context_metadata: evt.context_metadata || null,
              };
            } else {
              copy.push({ role: 'assistant', content: '', sources: evt.sources || [], context_metadata: evt.context_metadata || null });
            }
            return copy;
          });
          if (evt.session_id && evt.session_id !== sessionId) setSessionId(evt.session_id);
        },
        onError: (err) => {
          console.error('Error sending message:', err);
          setError('Failed to get response. Please try again.');
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.role === 'assistant' && !last.content) copy.pop();
            return copy;
          });
        },
      });
    } finally {
      setLoading(false);
      loadSessions(); // refresh sidebar (new chat appears / title updates)
    }
  };

  const handleTopicClick = useCallback((question) => {
    setInputExternalValue(`${question}?`);
  }, []);

  const handleNewChat = () => {
    setMessages([]);
    setSessionId(uuidv4());
    setError(null);
    setInputExternalValue('');
  };

  const handleSelectSession = async (sid) => {
    if (sid === sessionId) return;
    try {
      const data = await chatAPI.getSession(sid);
      const loaded = (data.messages || []).map((m) => ({ role: m.role, content: m.content }));
      setMessages(loaded);
      setSessionId(sid);
      setError(null);
    } catch {
      setError('Could not open that chat.');
    }
  };

  const handleDeleteSession = async (sid, e) => {
    e.stopPropagation();
    try {
      await chatAPI.deleteSession(sid);
      if (sid === sessionId) handleNewChat();
      loadSessions();
    } catch {
      setError('Could not delete that chat.');
    }
  };

  const handleStartRename = (s, e) => {
    e.stopPropagation();
    setEditingId(s.session_id);
    setEditingTitle(s.title);
  };

  const handleSaveRename = async (sid) => {
    const title = editingTitle.trim();
    setEditingId(null);
    if (!title) return;
    try {
      await chatAPI.renameSession(sid, title);
      loadSessions();
    } catch {
      setError('Could not rename that chat.');
    }
  };

  return (
    <Box sx={{ display: 'flex', height: 'calc(100vh - 64px)' }}>
      {/* History sidebar */}
      <Box sx={{ width: 270, borderRight: 1, borderColor: 'divider', bgcolor: '#f7f9fc', display: 'flex', flexDirection: 'column' }}>
        <Box sx={{ p: 1.5 }}>
          <Button fullWidth variant="contained" startIcon={<Add />} onClick={handleNewChat}>
            New Chat
          </Button>
        </Box>
        <Divider />
        <List dense sx={{ overflowY: 'auto', flexGrow: 1 }}>
          {sessions.length === 0 && (
            <Typography variant="body2" sx={{ p: 2, color: 'text.secondary' }}>
              No past chats yet.
            </Typography>
          )}
          {sessions.map((s) => (
            <ListItem
              key={s.session_id}
              disablePadding
              sx={{
                // Option A: the rename/delete icons are hidden and revealed only when the row is
                // hovered — so the title uses the full width and truncates cleanly with an ellipsis
                // (no more icon/title overlap). pointerEvents:none while hidden so an invisible icon
                // can never catch a stray click.
                '& .rowActions': { opacity: 0, pointerEvents: 'none', transition: 'opacity .15s ease' },
                '&:hover .rowActions': { opacity: 1, pointerEvents: 'auto' },
                // On hover, softly fade the title's right edge so the icons sit over blank space,
                // never on top of the text.
                '&:hover .MuiListItemText-primary': {
                  maskImage: 'linear-gradient(to right, #000 62%, transparent 92%)',
                  WebkitMaskImage: 'linear-gradient(to right, #000 62%, transparent 92%)',
                },
                // Touch devices have no hover — keep the icons visible so they stay reachable.
                '@media (hover: none)': {
                  '& .rowActions': { opacity: 1, pointerEvents: 'auto' },
                },
              }}
              secondaryAction={
                editingId === s.session_id ? null : (
                  <Box className="rowActions" sx={{ display: 'flex' }}>
                    <Tooltip title="Rename">
                      <IconButton edge="end" size="small" onClick={(e) => handleStartRename(s, e)}>
                        <Edit fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Delete">
                      <IconButton edge="end" size="small" onClick={(e) => handleDeleteSession(s.session_id, e)}>
                        <Delete fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                )
              }
            >
              {editingId === s.session_id ? (
                <Box sx={{ px: 1, py: 0.5, width: '100%' }}>
                  <TextField
                    size="small"
                    fullWidth
                    autoFocus
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    onBlur={() => handleSaveRename(s.session_id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleSaveRename(s.session_id);
                      if (e.key === 'Escape') setEditingId(null);
                    }}
                  />
                </Box>
              ) : (
                <ListItemButton selected={s.session_id === sessionId} onClick={() => handleSelectSession(s.session_id)}>
                  <ListItemText primary={s.title} primaryTypographyProps={{ noWrap: true, fontSize: 14 }} />
                </ListItemButton>
              )}
            </ListItem>
          ))}
        </List>
      </Box>

      {/* Chat area */}
      <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <Paper elevation={3} sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider', backgroundColor: '#e6f0ff' }}>
            <Typography variant="h5" sx={{ color: '#0d76ff', fontWeight: 600 }}>
              Transcription Assistant
            </Typography>
          </Box>

          {error && (
            <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2 }}>
              {error}
            </Alert>
          )}

          {messages.length === 0 ? (
            <div className="welcome-screen">
              <div className="welcome-screen__icon"><span style={{ fontSize: '2rem' }}>💬</span></div>
              <div className="welcome-screen__title">Ask anything about transcripts or requirements</div>
              <div className="welcome-screen__subtitle">Just type your question to search through recorded sessions and system requirements.</div>
            </div>
          ) : (
            <MessageList messages={messages} onTopicClick={handleTopicClick} />
          )}

          {loading && messages[messages.length - 1]?.role !== 'assistant' && <TypingIndicator />}

          <MessageInput
            onSendMessage={handleSendMessage}
            disabled={loading}
            externalValue={inputExternalValue}
            onExternalValueConsumed={() => setInputExternalValue('')}
          />
        </Paper>
      </Box>
    </Box>
  );
}

export default ChatInterface;

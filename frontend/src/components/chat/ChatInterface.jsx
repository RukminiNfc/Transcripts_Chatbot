import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Box, Typography, Alert, TextField, InputAdornment, List, ListItem, ListItemButton,
  ListItemIcon, ListItemText, ListSubheader, IconButton, Menu, MenuItem, useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import {
  Delete, Edit, MoreHoriz, Search, ChatBubbleOutline, ArrowBack, ForumOutlined,
} from '@mui/icons-material';
import { useLocation } from 'react-router-dom';
import { v4 as uuidv4 } from 'uuid';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import TypingIndicator from './TypingIndicator';
import { chatAPI } from '../../services/api';
import { tokens } from '../../theme/theme';
import './ChatStyles.css';


// Backend timestamps are naive UTC ("2026-10-06T11:20:00"); treat them as UTC.
const parseTs = (ts) => {
  if (!ts) return null;
  const d = new Date(/Z|[+-]\d{2}:?\d{2}$/.test(ts) ? ts : `${ts}Z`);
  return isNaN(d) ? null : d;
};

const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

// Today / Yesterday / This Week / Older, by last activity.
const groupLabel = (d) => {
  if (!d) return 'Older';
  const days = Math.round((startOfDay(new Date()) - startOfDay(d)) / 86400000);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return 'This Week';
  return 'Older';
};
const GROUP_ORDER = ['Today', 'Yesterday', 'This Week', 'Older'];

// Right-hand time on a list row: "11:20 AM" today, "Wed" this week, "Sep 28" before that.
const shortTime = (d) => {
  if (!d) return '';
  const label = groupLabel(d);
  if (label === 'Today') return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  if (label === 'Yesterday' || label === 'This Week') return d.toLocaleDateString(undefined, { weekday: 'short' });
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

function ChatInterface() {
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));

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
  const [search, setSearch] = useState('');
  const [menu, setMenu] = useState(null); // { anchor, session } for the row "⋯" menu
  // Phones show one pane at a time: the conversation list or the open thread.
  const [mobileView, setMobileView] = useState('thread');

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

  // The sidebar's "New Chat" button navigates here with { newChat: <timestamp> }.
  const location = useLocation();
  const newChatSignal = location.state?.newChat;
  useEffect(() => {
    if (newChatSignal) handleNewChat();
  }, [newChatSignal]);

  // Other pages (e.g. Requirements → "Ask in Chat") navigate here with { prefill: '<question>' }:
  // a fresh chat opens with the question in the input, ready to edit or send.
  const prefill = location.state?.prefill;
  useEffect(() => {
    if (prefill) startWithQuestion(prefill);
  }, [prefill]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSendMessage = async (messageText) => {
    setError(null);
    setLoading(true);
    setMessages((prev) => [...prev, { role: 'user', content: messageText, timestamp: new Date().toISOString() }]);

    const appendToAssistant = (text) => {
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === 'assistant' && last.streaming) {
          copy[copy.length - 1] = { ...last, content: last.content + text };
        } else {
          copy.push({ role: 'assistant', content: text, streaming: true, timestamp: new Date().toISOString() });
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
              copy.push({ role: 'assistant', content: '', sources: evt.sources || [], context_metadata: evt.context_metadata || null, timestamp: new Date().toISOString() });
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
    setMobileView('thread');
  };

  const startWithQuestion = (question) => {
    handleNewChat();
    setInputExternalValue(question);
  };

  const handleSelectSession = async (sid) => {
    if (sid === sessionId) { setMobileView('thread'); return; }
    try {
      const data = await chatAPI.getSession(sid);
      const loaded = (data.messages || []).map((m) => ({ role: m.role, content: m.content, timestamp: m.timestamp }));
      setMessages(loaded);
      setSessionId(sid);
      setError(null);
      setMobileView('thread');
    } catch {
      setError('Could not open that chat.');
    }
  };

  const handleDeleteSession = async (sid) => {
    try {
      await chatAPI.deleteSession(sid);
      if (sid === sessionId) handleNewChat();
      loadSessions();
    } catch {
      setError('Could not delete that chat.');
    }
  };

  const handleStartRename = (s) => {
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

  // Filter by title, then bucket into date groups (list is already newest-first from the API).
  const grouped = useMemo(() => {
    const q = search.trim().toLowerCase();
    const groups = {};
    sessions
      .filter((s) => !q || (s.title || '').toLowerCase().includes(q))
      .forEach((s) => {
        const d = parseTs(s.last_activity);
        const label = groupLabel(d);
        (groups[label] ||= []).push({ ...s, _date: d });
      });
    return GROUP_ORDER.filter((g) => groups[g]).map((g) => ({ label: g, items: groups[g] }));
  }, [sessions, search]);

  const current = sessions.find((s) => s.session_id === sessionId);
  const threadTitle = current?.title || 'New chat';
  const threadDate = parseTs(current?.last_activity);

  const showList = isDesktop || mobileView === 'list';
  const showThread = isDesktop || mobileView === 'thread';

  return (
    <Box sx={{ display: 'flex', height: '100%', bgcolor: 'background.default' }}>
      {/* Conversation list */}
      {showList && (
        <Box sx={{
          width: isDesktop ? tokens.chatListWidth : '100%', flexShrink: 0, borderRight: isDesktop ? 1 : 0,
          borderColor: 'divider', display: 'flex', flexDirection: 'column', minHeight: 0,
        }}>
          <Box sx={{ px: 2.5, pt: { xs: 2, xl: 3 }, pb: 1.5 }}>
            <Typography variant="h3" component="h1" sx={{ mb: { xs: 1.5, xl: 2 } }}>Chat</Typography>
            <TextField
              size="small"
              fullWidth
              placeholder="Search conversations..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              slotProps={{
                input: {
                  startAdornment: <InputAdornment position="start"><Search fontSize="small" /></InputAdornment>,
                },
                htmlInput: { 'aria-label': 'Search conversations' },
              }}
            />
          </Box>

          <List dense sx={{ overflowY: 'auto', flexGrow: 1, px: 1, pb: 2 }}>
            {sessions.length === 0 && (
              <Typography variant="body2" sx={{ p: 2, color: 'text.secondary' }}>
                No past chats yet.
              </Typography>
            )}
            {sessions.length > 0 && grouped.length === 0 && (
              <Typography variant="body2" sx={{ p: 2, color: 'text.secondary' }}>
                No chats match “{search}”.
              </Typography>
            )}
            {grouped.map((group) => (
              <React.Fragment key={group.label}>
                <ListSubheader disableSticky sx={{ bgcolor: 'transparent', lineHeight: '36px', px: 1.5, color: 'text.secondary', fontWeight: 500 }}>
                  {group.label}
                </ListSubheader>
                {group.items.map((s) => (
                  <ListItem
                    key={s.session_id}
                    disablePadding
                    sx={{ mb: 0.25 }}
                    secondaryAction={
                      editingId === s.session_id ? null : (
                        <IconButton
                          edge="end"
                          size="small"
                          aria-label={`Actions for ${s.title}`}
                          onClick={(e) => { e.stopPropagation(); setMenu({ anchor: e.currentTarget, session: s }); }}
                        >
                          <MoreHoriz fontSize="small" />
                        </IconButton>
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
                      <ListItemButton
                        selected={s.session_id === sessionId}
                        onClick={() => handleSelectSession(s.session_id)}
                        sx={{ pr: 6, py: 1 }}
                      >
                        <ListItemIcon sx={{ minWidth: '2rem', color: 'text.secondary' }}>
                          <ChatBubbleOutline fontSize="small" />
                        </ListItemIcon>
                        <ListItemText
                          primary={s.title}
                          secondary={shortTime(s._date)}
                          slotProps={{
                            primary: { noWrap: true, fontWeight: 500, sx: { fontSize: { xs: '0.8125rem', xl: '0.8438rem' } } },
                            secondary: { fontSize: '0.7188rem' },
                          }}
                        />
                      </ListItemButton>
                    )}
                  </ListItem>
                ))}
              </React.Fragment>
            ))}
          </List>

          <Menu anchorEl={menu?.anchor} open={!!menu} onClose={() => setMenu(null)}>
            <MenuItem onClick={() => { handleStartRename(menu.session); setMenu(null); }}>
              <ListItemIcon><Edit fontSize="small" /></ListItemIcon>
              Rename
            </MenuItem>
            <MenuItem onClick={() => { handleDeleteSession(menu.session.session_id); setMenu(null); }} sx={{ color: 'error.main' }}>
              <ListItemIcon><Delete fontSize="small" color="error" /></ListItemIcon>
              Delete
            </MenuItem>
          </Menu>
        </Box>
      )}

      {/* Thread */}
      {showThread && (
        <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column', bgcolor: tokens.card }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: { xs: 1.5, md: 4 }, pt: { xs: 1.5, md: 2, xl: 3 }, pb: 1.5, borderBottom: 1, borderColor: 'divider' }}>
            {!isDesktop && (
              <IconButton onClick={() => setMobileView('list')} aria-label="Back to conversations">
                <ArrowBack />
              </IconButton>
            )}
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="h4" component="h2" noWrap sx={{ fontSize: { xs: '1.05rem', md: '1.15rem' } }}>
                {threadTitle}
              </Typography>
              {threadDate && (
                <Typography variant="caption" color="text.secondary">
                  {threadDate.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                </Typography>
              )}
            </Box>
          </Box>

          {error && (
            <Alert severity="error" onClose={() => setError(null)} sx={{ mx: { xs: 2, md: 4 }, mt: 2 }}>
              {error}
            </Alert>
          )}

          {messages.length === 0 ? (
            <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 1.5, px: 3, textAlign: 'center' }}>
              <Box sx={{ width: '4rem', height: '4rem', borderRadius: '50%', display: 'grid', placeItems: 'center', bgcolor: 'secondary.light', color: 'secondary.main' }}>
                <ForumOutlined />
              </Box>
              <Typography variant="h4" component="p">Ask anything about your meetings</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 420 }}>
                Search across recorded sessions, minutes and requirements.
              </Typography>
            </Box>
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
        </Box>
      )}
    </Box>
  );
}

export default ChatInterface;

import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Container,
  Paper,
  Typography,
  Button,
  Alert,
} from '@mui/material';
import { v4 as uuidv4 } from 'uuid';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import TypingIndicator from './TypingIndicator';
import { chatAPI } from '../../services/api';
import './ChatStyles.css';

// Welcome screen starter prompts removed per user request

function ChatInterface() {
  // Restore the conversation from sessionStorage so switching to Admin/Requirements
  // (which unmounts this component) or refreshing the page does not lose it.
  const [messages, setMessages] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem('chat_messages')) || []; }
    catch { return []; }
  });
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem('chat_session_id') || null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // NEW: External value for MessageInput (populated when topic suggestion is clicked)
  const [inputExternalValue, setInputExternalValue] = useState('');

  // Create a session id only if we don't already have one restored from storage.
  useEffect(() => {
    if (!sessionId) setSessionId(uuidv4());
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Persist the conversation + session id whenever they change.
  useEffect(() => {
    sessionStorage.setItem('chat_messages', JSON.stringify(messages));
  }, [messages]);

  useEffect(() => {
    if (sessionId) sessionStorage.setItem('chat_session_id', sessionId);
  }, [sessionId]);

  const handleSendMessage = async (messageText) => {
    setError(null);
    setLoading(true);

    // Add the user message. The assistant bubble is created when the first token arrives,
    // so the typing indicator shows during the pre-stream "thinking" phase.
    setMessages((prev) => [...prev, { role: 'user', content: messageText }]);

    // Append streamed text: create the assistant bubble on the first delta, then grow it.
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
                ...last,
                streaming: false,
                sources: evt.sources || [],
                context_metadata: evt.context_metadata || null,
              };
            } else {
              // No tokens streamed (empty answer) — still show a (blank) assistant bubble.
              copy.push({ role: 'assistant', content: '', sources: evt.sources || [], context_metadata: evt.context_metadata || null });
            }
            return copy;
          });
          if (evt.session_id && evt.session_id !== sessionId) setSessionId(evt.session_id);
        },
        onError: (err) => {
          console.error('Error sending message:', err);
          setError('Failed to get response. Please try again.');
          // Remove a half-formed assistant bubble if it has no content.
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
    }
  };

  // Handle topic suggestion click → populate input bar
  const handleTopicClick = useCallback((question) => {
    setInputExternalValue(`${question}?`);
  }, []);

  const handleClearChat = () => {
    setMessages([]);
    setSessionId(uuidv4());
    setError(null);
    setInputExternalValue('');
  };

  return (
    <Container maxWidth="lg" sx={{ height: 'calc(100vh - 64px)', display: 'flex', flexDirection: 'column', py: 2 }}>
      <Paper elevation={3} sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Header — Clean, no dropdowns */}
        <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider', backgroundColor: '#e6f0ff', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="h5" sx={{ color: '#0d76ff', fontWeight: 600 }}>
            Transcription Assistant
          </Typography>
          <Button
            variant="outlined"
            size="small"
            onClick={handleClearChat}
            sx={{
              borderColor: '#0d76ff',
              color: '#0d76ff',
              '&:hover': { backgroundColor: '#e6f0ff', borderColor: '#0d76ff' }
            }}
          >
            Clear Chat
          </Button>
        </Box>

        {/* Error Alert */}
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2 }}>
            {error}
          </Alert>
        )}

        {/* Messages or Welcome Screen */}
        {messages.length === 0 ? (
          <div className="welcome-screen">
            <div className="welcome-screen__icon">
              <span style={{ fontSize: '2rem' }}>💬</span>
            </div>
            <div className="welcome-screen__title">
              Ask anything about transcripts or requirements
            </div>
            <div className="welcome-screen__subtitle">
              Just type your question to search through recorded sessions and system requirements.
            </div>
          </div>
        ) : (
          <MessageList
            messages={messages}
            onTopicClick={handleTopicClick}
          />
        )}

        {/* Typing indicator — only during the pre-stream "thinking" phase (before the
            assistant bubble appears); once tokens stream, the bubble itself shows progress. */}
        {loading && messages[messages.length - 1]?.role !== 'assistant' && <TypingIndicator />}

        {/* Input */}
        <MessageInput
          onSendMessage={handleSendMessage}
          disabled={loading}
          externalValue={inputExternalValue}
          onExternalValueConsumed={() => setInputExternalValue('')}
        />
      </Paper>
    </Container>
  );
}

export default ChatInterface;
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
import CountryContextBar from './CountryContextBar';
import CountrySelector from './CountrySelector';
import { chatAPI } from '../../services/api';
import './ChatStyles.css';

// Welcome screen starter prompts
const WELCOME_PROMPTS = [
  { text: "How does trademark registration work in India?", icon: "🇮🇳" },
  { text: "What are grounds for trademark cancellation?", icon: "⚖️" },
  { text: "Compare renewal processes across countries", icon: "🌍" },
  { text: "What is trademark opposition?", icon: "📋" },
];

function ChatInterface() {
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // NEW: Context-aware state (replaces dropdown filters)
  const [countryContext, setCountryContext] = useState(null); // Locked country name or null
  const [showCountrySelector, setShowCountrySelector] = useState(false);

  // NEW: External value for MessageInput (populated when topic suggestion is clicked)
  const [inputExternalValue, setInputExternalValue] = useState('');

  useEffect(() => {
    setSessionId(uuidv4());
  }, []);

  const handleSendMessage = async (messageText) => {
    setError(null);
    setLoading(true);

    // Add user message
    const userMessage = {
      role: 'user',
      content: messageText,
    };
    setMessages((prev) => [...prev, userMessage]);

    try {
      // Build filters based on locked country context (replaces dropdown filters)
      const activeFilters = {};
      if (countryContext) {
        activeFilters.jurisdiction = countryContext;
      }

      // Send to API
      const response = await chatAPI.sendMessage(
        messageText,
        sessionId,
        Object.keys(activeFilters).length > 0 ? activeFilters : null
      );

      // Add assistant message with context_metadata attached
      const assistantMessage = {
        role: 'assistant',
        content: response.answer,
        sources: response.sources,
        context_metadata: response.context_metadata || null,
      };
      setMessages((prev) => [...prev, assistantMessage]);

      // Auto-lock country if system detected a country-specific query
      // (only if not already locked)
      if (
        !countryContext &&
        response.context_metadata?.query_type === 'country_specific' &&
        response.context_metadata?.detected_country
      ) {
        setCountryContext(response.context_metadata.detected_country);
      }

      // Update session ID if new
      if (response.session_id && response.session_id !== sessionId) {
        setSessionId(response.session_id);
      }
    } catch (err) {
      console.error('Error sending message:', err);
      setError('Failed to get response. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Handle topic suggestion click → populate input bar
  const handleTopicClick = useCallback((question) => {
    // Backend provides base question (e.g., "What is the registration procedure")
    // We just need to add context and punctuation.
    const finalQuestion = countryContext
      ? `${question} in ${countryContext}?`
      : `${question}?`;
    setInputExternalValue(finalQuestion);
  }, [countryContext]);

  // Handle guide suggestion click
  const handleGuideClick = useCallback((guide) => {
    // New format from dynamic GuideSuggestions
    if (guide.type === 'country') {
      setCountryContext(guide.value);
      // Optional: also populate input to prompt user? 
      // User said "lock to particular country", so context lock is primary.
      // We could add a system message or toast, but the context bar will appear.
      return;
    }

    // Old static guide actions (keep for backward compatibility if needed)
    if (guide.action === 'select_country') {
      setShowCountrySelector(true);
    } else if (guide.action === 'compare') {
      setInputExternalValue('Compare trademark laws across countries');
    } else if (guide.action === 'breakdown') {
      setInputExternalValue('Give me a country-wise breakdown');
    }
  }, []);

  // Handle country selection from the modal
  const handleCountrySelect = useCallback((country) => {
    setCountryContext(country);
    setShowCountrySelector(false);
  }, []);

  // Change country — opens selector
  const handleChangeCountry = useCallback(() => {
    setShowCountrySelector(true);
  }, []);

  // Reset to global context
  const handleResetContext = useCallback(() => {
    setCountryContext(null);
  }, []);

  const handleClearChat = () => {
    setMessages([]);
    setSessionId(uuidv4());
    setError(null);
    setCountryContext(null);
    setInputExternalValue('');
  };

  return (
    <Container maxWidth="lg" sx={{ height: '100vh', display: 'flex', flexDirection: 'column', py: 2 }}>
      <Paper elevation={3} sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Header — Clean, no dropdowns */}
        <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider', backgroundColor: '#fff3e0', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="h5" sx={{ color: '#ff6900', fontWeight: 600 }}>
            INTA Document Assistant
          </Typography>
          <Button
            variant="outlined"
            size="small"
            onClick={handleClearChat}
            sx={{
              borderColor: '#ff6900',
              color: '#ff6900',
              '&:hover': { backgroundColor: '#fff3e0', borderColor: '#ff6900' }
            }}
          >
            Clear Chat
          </Button>
        </Box>

        {/* Country Context Bar — shows when a country is locked */}
        <CountryContextBar
          country={countryContext}
          onChangeCountry={handleChangeCountry}
          onReset={handleResetContext}
        />

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
              Ask anything about trademark documents
            </div>
            <div className="welcome-screen__subtitle">
              Just type your question — I'll figure out the country and suggest related topics automatically.
            </div>
            <div className="welcome-screen__prompts">
              {WELCOME_PROMPTS.map((prompt, idx) => (
                <button
                  key={idx}
                  className="welcome-prompt"
                  onClick={() => handleSendMessage(prompt.text)}
                >
                  <span className="welcome-prompt__icon">{prompt.icon}</span>
                  {prompt.text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <MessageList
            messages={messages}
            onTopicClick={handleTopicClick}
            onGuideClick={handleGuideClick}
          />
        )}

        {/* Loading Indicator */}
        {loading && <TypingIndicator />}

        {/* Input */}
        <MessageInput
          onSendMessage={handleSendMessage}
          disabled={loading}
          externalValue={inputExternalValue}
          onExternalValueConsumed={() => setInputExternalValue('')}
        />
      </Paper>

      {/* Country Selector Modal */}
      <CountrySelector
        open={showCountrySelector}
        onSelect={handleCountrySelect}
        onClose={() => setShowCountrySelector(false)}
      />
    </Container>
  );
}

export default ChatInterface;
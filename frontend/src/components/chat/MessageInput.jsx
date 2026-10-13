import React, { useState, useEffect } from 'react';
import { Box, InputBase, IconButton } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { ArrowUpward } from '@mui/icons-material';
import './ChatStyles.css';

/**
 * MessageInput - Enhanced with external value control for topic suggestion prefill.
 * When a topic suggestion is clicked, the parent sets `externalValue` to populate the input.
 * The user can always edit before sending.
 */
function MessageInput({ onSendMessage, disabled, externalValue, onExternalValueConsumed }) {
  const [message, setMessage] = useState('');
  const [isSuggested, setIsSuggested] = useState(false);

  // When externalValue changes (topic suggestion clicked), populate the input
  useEffect(() => {
    if (externalValue) {
      setMessage(externalValue);
      setIsSuggested(true);
      // Notify parent that we consumed the external value
      if (onExternalValueConsumed) {
        onExternalValueConsumed();
      }
    }
  }, [externalValue]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (message.trim()) {
      onSendMessage(message);
      setMessage('');
      setIsSuggested(false);
    }
  };

  const handleChange = (e) => {
    setMessage(e.target.value);
    // Once user edits, remove the "suggested" badge
    if (isSuggested) {
      setIsSuggested(false);
    }
  };

  return (
    // Bottom padding includes the iPhone home-indicator safe area.
    <Box sx={{ px: { xs: 1.5, md: 4 }, pt: 1, pb: 'calc(16px + env(safe-area-inset-bottom))', position: 'relative' }}>
      {isSuggested && (
        <span className="message-input-suggestion-badge">
          SUGGESTED — edit or press Enter
        </span>
      )}
      <Box
        component="form"
        onSubmit={handleSubmit}
        sx={{
          display: 'flex', alignItems: 'flex-end', gap: 1, pl: 2, pr: 0.75, py: 0.75,
          bgcolor: 'background.paper', borderRadius: 3, border: 1,
          borderColor: isSuggested ? 'secondary.main' : 'divider',
          boxShadow: (t) => `0 1px 3px ${alpha(t.palette.primary.main, 0.08)}`,
          '&:focus-within': { borderColor: 'secondary.main' },
        }}
      >
        <InputBase
          fullWidth
          placeholder="Ask a question about your meetings, requirements, or documents..."
          value={message}
          onChange={handleChange}
          onKeyDown={(e) => {
            // Enter = send; Shift+Enter = new line (standard chat behavior). The field is multiline,
            // so by default Enter would only insert a newline — this makes Enter submit instead.
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              if (!disabled) handleSubmit(e);
            }
          }}
          disabled={disabled}
          multiline
          maxRows={5}
          inputProps={{ 'aria-label': 'Message' }}
          // 16px on phones: iOS Safari zooms the page when focusing an input smaller than that.
          sx={{ py: 0.5, fontSize: { xs: '1rem', md: '0.8438rem' } }}
        />
        <IconButton
          type="submit"
          aria-label="Send"
          disabled={disabled || !message.trim()}
          sx={{
            bgcolor: 'primary.main', color: 'primary.contrastText', width: '2.5rem', height: '2.5rem',
            '&:hover': { bgcolor: 'primary.dark' },
            '&.Mui-disabled': { bgcolor: 'divider', color: 'text.disabled' },
          }}
        >
          <ArrowUpward fontSize="small" />
        </IconButton>
      </Box>
    </Box>
  );
}

export default MessageInput;

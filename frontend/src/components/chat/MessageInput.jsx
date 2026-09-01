import React, { useState, useEffect } from 'react';
import { Box, TextField, IconButton, Paper } from '@mui/material';
import { Send } from '@mui/icons-material';
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
    <Paper elevation={3} sx={{ p: 2, position: 'relative' }}>
      {isSuggested && (
        <span className="message-input-suggestion-badge">
          SUGGESTED — edit or press Enter
        </span>
      )}
      <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', gap: 1 }}>
        <TextField
          fullWidth
          placeholder="Ask a question about transcripts or requirements..."
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
          maxRows={4}
          sx={isSuggested ? {
            '& .MuiOutlinedInput-root': {
              borderColor: '#0d76ff',
              '& fieldset': {
                borderColor: '#0d76ff',
                borderWidth: '2px',
              }
            }
          } : {}}
        />
        <IconButton
          type="submit"
          color="primary"
          disabled={disabled || !message.trim()}
          sx={{
            alignSelf: 'flex-end',
            backgroundColor: '#0d76ff',
            color: 'white',
            '&:hover': { backgroundColor: '#0a58cc' },
            '&.Mui-disabled': { backgroundColor: '#ccc', color: '#999' }
          }}
        >
          <Send />
        </IconButton>
      </Box>
    </Paper>
  );
}

export default MessageInput;
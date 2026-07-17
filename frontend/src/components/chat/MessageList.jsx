import React, { useRef, useEffect, useState } from 'react';
import { Box, Paper, Typography, Chip, Tooltip, Dialog, DialogTitle, DialogContent, DialogActions, Button } from '@mui/material';
import { Person, SmartToy, Description } from '@mui/icons-material';
import MarkdownRenderer from './MarkdownRenderer';
import TopicSuggestions from './TopicSuggestions';

function MessageList({ messages, onTopicClick }) {
  const messagesEndRef = useRef(null);
  const [visibleSuggestions, setVisibleSuggestions] = useState(new Set());
  const [selectedSource, setSelectedSource] = useState(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, visibleSuggestions]);

  // Effect to delay showing suggestions for the latest message
  useEffect(() => {
    if (messages.length > 0) {
      const lastMessageIndex = messages.length - 1;
      const lastMessage = messages[lastMessageIndex];

      // Only animate if it's an assistant message and not already visible
      if (lastMessage.role === 'assistant' && !visibleSuggestions.has(lastMessageIndex)) {
        const timer = setTimeout(() => {
          setVisibleSuggestions(prev => {
            const newSet = new Set(prev);
            newSet.add(lastMessageIndex);
            return newSet;
          });
        }, 1000); // 1 second delay

        return () => clearTimeout(timer);
      }
    }
  }, [messages]);


  return (
    <Box sx={{ flexGrow: 1, overflowY: 'auto', p: 2 }}>
      {messages.map((message, index) => (
        <Box
          key={index}
          sx={{
            display: 'flex',
            justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start',
            mb: 2,
          }}
        >
          {/* Wrapper for vertical layout of bubble + suggestions */}
          <Box sx={{ display: 'flex', flexDirection: 'column', maxWidth: '70%', alignItems: message.role === 'user' ? 'flex-end' : 'flex-start' }}>
            <Paper
              elevation={2}
              sx={{
                p: 2,
                width: '100%',
                backgroundColor: message.role === 'user' ? '#0d76ff' : '#f5f5f5',
                color: message.role === 'user' ? '#ffffff' : '#000000',
                transition: 'all 0.3s ease'
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                {message.role === 'user' ? (
                  <Person sx={{ mr: 1, color: message.role === 'user' ? '#ffffff' : '#0d76ff' }} />
                ) : (
                  <SmartToy sx={{ mr: 1, color: '#0d76ff' }} />
                )}
                <Typography variant="subtitle2" fontWeight="bold">
                  {message.role === 'user' ? 'You' : 'Assistant'}
                </Typography>
              </Box>

              {/* Render message content with markdown support for bot messages */}
              {message.role === 'assistant' ? (
                <MarkdownRenderer content={message.content} />
              ) : (
                <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap' }}>
                  {message.content}
                </Typography>
              )}

              {/* Sources section removed per user request */}
            </Paper>

            {/* Suggestions Rendered Outside Bubble */}
            <Box sx={{ width: '100%', mt: 1, pl: 1 }}>
              {/* Topic Suggestions - delayed rendering */}
              {message.role === 'assistant' &&
                index === messages.length - 1 &&
                message.context_metadata?.suggested_topics &&
                visibleSuggestions.has(index) && (
                  <div style={{ animation: 'fadeInUp 0.5s ease-out' }}>
                    <TopicSuggestions
                      suggestions={message.context_metadata.suggested_topics}
                      onTopicClick={onTopicClick}
                    />
                  </div>
                )}


            </Box>
          </Box>
        </Box>
      ))}
      <div ref={messagesEndRef} />

      {/* Source Details Dialog */}
      <Dialog 
        open={Boolean(selectedSource)} 
        onClose={() => setSelectedSource(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 1, borderBottom: '1px solid #eee' }}>
          {selectedSource?.type === 'conversation' 
            ? `Transcript: ${selectedSource?.session}`
            : 'Requirement Source'}
        </DialogTitle>
        <DialogContent sx={{ mt: 2 }}>
          {selectedSource?.type === 'conversation' && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" color="text.secondary">Speaker: {selectedSource?.speaker}</Typography>
              <Typography variant="subtitle2" color="text.secondary">Timestamp: {selectedSource?.timestamp}</Typography>
            </Box>
          )}
          {selectedSource?.type === 'requirement' && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" color="text.secondary">Category: {selectedSource?.category} › {selectedSource?.sub_category}</Typography>
              <Typography variant="subtitle2" color="text.secondary">Confirmed By: {selectedSource?.confirmed_by}</Typography>
            </Box>
          )}
          <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap', p: 2, bgcolor: '#f8f9fa', borderRadius: 1 }}>
            {selectedSource?.text}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelectedSource(null)} sx={{ color: '#0d76ff' }}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export default MessageList;
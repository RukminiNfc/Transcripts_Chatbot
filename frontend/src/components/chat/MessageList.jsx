import React, { useRef, useEffect, useState } from 'react';
import { Box, Paper, Typography, Chip, Tooltip } from '@mui/material';
import { Person, SmartToy, Download, Description } from '@mui/icons-material';
import { documentAPI } from '../../services/api';
import MarkdownRenderer from './MarkdownRenderer';
import TopicSuggestions from './TopicSuggestions';
import GuideSuggestions from './GuideSuggestions';

function MessageList({ messages, onTopicClick, onGuideClick }) {
  const messagesEndRef = useRef(null);
  const [visibleSuggestions, setVisibleSuggestions] = useState(new Set());

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

  const handleDownload = (source) => {
    if (source.document_id) {
      window.open(documentAPI.getDownloadUrl(source.document_id), '_blank');
    } else {
      console.warn('No document_id available for download');
    }
  };

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
                backgroundColor: message.role === 'user' ? '#ff6900' : '#f5f5f5',
                color: message.role === 'user' ? '#ffffff' : '#000000',
                transition: 'all 0.3s ease'
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                {message.role === 'user' ? (
                  <Person sx={{ mr: 1, color: message.role === 'user' ? '#ffffff' : '#ff6900' }} />
                ) : (
                  <SmartToy sx={{ mr: 1, color: '#ff6900' }} />
                )}
                <Typography variant="subtitle2" fontWeight="bold">
                  {message.role === 'user' ? 'You' : 'INTA Assistant'}
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

              {/* Sources with Download Buttons - Deduplicated by document */}
              {message.sources && message.sources.length > 0 && (() => {
                // Deduplicate sources by document_id
                const uniqueSources = message.sources.reduce((acc, source) => {
                  const key = source.document_id || source.document_title;
                  if (!acc.find(s => (s.document_id || s.document_title) === key)) {
                    acc.push(source);
                  }
                  return acc;
                }, []);

                return (
                  <Box sx={{ mt: 2, pt: 2, borderTop: '1px solid #ddd' }}>
                    <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
                      Source:
                    </Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                      {uniqueSources.map((source, idx) => (
                        <Tooltip
                          key={idx}
                          title={`Download: ${source.document_title} (${source.jurisdiction || 'Unknown'})`}
                        >
                          <Chip
                            icon={<Description fontSize="small" />}
                            label={`${source.jurisdiction || 'Doc'} - ${source.document_title?.substring(0, 25)}${source.document_title?.length > 25 ? '...' : ''}`}
                            size="small"
                            variant="outlined"
                            onClick={() => handleDownload(source)}
                            onDelete={() => handleDownload(source)}
                            deleteIcon={<Download fontSize="small" />}
                            sx={{
                              cursor: 'pointer',
                              borderColor: '#ff6900',
                              color: '#ff6900',
                              '&:hover': {
                                backgroundColor: '#fff3e0',
                                borderColor: '#ff6900'
                              }
                            }}
                          />
                        </Tooltip>
                      ))}
                    </Box>
                  </Box>
                );
              })()}
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

              {/* Guide Suggestions - delayed rendering */}
              {message.role === 'assistant' &&
                index === messages.length - 1 &&
                message.context_metadata?.guide_suggestions &&
                visibleSuggestions.has(index) && (
                  <div style={{ animation: 'fadeInUp 0.5s ease-out' }}>
                    <GuideSuggestions
                      suggestions={message.context_metadata.guide_suggestions}
                      onGuideClick={onGuideClick}
                    />
                  </div>
                )}
            </Box>
          </Box>
        </Box>
      ))}
      <div ref={messagesEndRef} />
    </Box>
  );
}

export default MessageList;
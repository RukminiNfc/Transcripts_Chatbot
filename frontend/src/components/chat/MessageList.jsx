import React, { useRef, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Paper, Typography, Chip, Tooltip, Dialog, DialogTitle, DialogContent, DialogActions, Button, IconButton, CircularProgress } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { Description, ContentCopy, Check, Download, OpenInNew } from '@mui/icons-material';
import MarkdownRenderer from './MarkdownRenderer';
import { tokens } from '../../theme/theme';
import TopicSuggestions from './TopicSuggestions';
import { momAPI } from '../../services/api';
import { deriveTopics } from '../minutes/momParse';

/**
 * A bot answer with an always-visible copy button (top-right). Clicking it copies the CLEAN
 * rendered text (what the user sees — no markdown symbols) to the clipboard, and briefly shows a
 * check mark as confirmation. Self-contained so each answer keeps its own "copied" state.
 */
function CopyableAnswer({ content }) {
  const contentRef = useRef(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    // innerText = exactly what's shown on screen, without the ## / ** markdown symbols.
    const text = contentRef.current ? contentRef.current.innerText : (content || '');
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback for older browsers / non-secure contexts.
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Box sx={{ position: 'relative' }}>
      <Tooltip title={copied ? 'Copied!' : 'Copy'}>
        <IconButton
          size="small"
          onClick={handleCopy}
          aria-label="Copy answer"
          sx={{ position: 'absolute', top: -8, right: -8, color: copied ? 'secondary.main' : 'text.secondary' }}
        >
          {copied ? <Check fontSize="small" /> : <ContentCopy fontSize="small" />}
        </IconButton>
      </Tooltip>
      <Box ref={contentRef} sx={{ pr: 4 }}>
        <MarkdownRenderer content={content} />
      </Box>
    </Box>
  );
}

// "11:20 AM" for today, "Oct 6, 11:20 AM" otherwise. Backend timestamps are naive UTC.
const formatTime = (ts) => {
  if (!ts) return '';
  const d = new Date(/Z|[+-]\d{2}:?\d{2}$/.test(ts) ? ts : `${ts}Z`);
  if (isNaN(d)) return '';
  const sameDay = d.toDateString() === new Date().toDateString();
  return d.toLocaleString(undefined, sameDay
    ? { hour: 'numeric', minute: '2-digit' }
    : { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};

const clip = (text, max) => (text.length > max ? `${text.slice(0, max).trimEnd()}…` : text);

const MAX_TOPICS = 5;

/**
 * The Minutes card, shown when a reply carries context_metadata.minutes.
 *
 * It REPLACES the answer text rather than sitting under it: the backend's minutes reply is already
 * "title + header + preview", so rendering both would print the same preview twice. A full MOM runs
 * to tens of thousands of characters, so the chat shows a card and the document opens on its own
 * page.
 */
function MinutesCard({ minutes }) {
  const navigate = useNavigate();
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const [topics, setTopics] = useState(null);

  // The chat metadata carries only the first 400 characters of the document, which on a MOM that
  // opens with a table says very little. Fetch the body once and show its section list instead.
  // Purely an enhancement: if this fails, the card falls back to the preview it was handed.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await momAPI.get(minutes.id);
        if (!cancelled) setTopics(deriveTopics(data.content_markdown));
      } catch {
        if (!cancelled) setTopics([]);
      }
    })();
    return () => { cancelled = true; };
  }, [minutes.id]);

  // A date-only string ('2026-05-04') parses as UTC midnight, which displays as the PREVIOUS day
  // in any behind-UTC timezone. Pin it to local midnight instead. Full timestamps are fine as-is.
  const raw = minutes.call_date;
  const parsed = raw ? new Date(/^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00` : raw) : null;
  const dateText = parsed && !isNaN(parsed)
    ? parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
    : '';

  const handleDownload = async () => {
    setDownloading(true);
    setError('');
    try {
      await momAPI.download(minutes.id, minutes.session_name);
    } catch (err) {
      setError(err.message || 'Download failed.');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Paper variant="outlined" sx={{ bgcolor: 'background.paper', borderRadius: 2, overflow: 'hidden' }}>
      <Box sx={{ px: 2, py: 1.25, bgcolor: tokens.sageSoft, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="subtitle2" fontWeight={700} sx={{ color: 'primary.main', display: 'flex', alignItems: 'center', gap: 0.75 }}>
          <Description fontSize="small" sx={{ color: 'secondary.main' }} /> Minutes of Meeting
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {[minutes.session_name, dateText].filter(Boolean).join(' · ')}
        </Typography>
        {minutes.truncated && (
          <Tooltip title="These minutes reached the generation length limit and may be incomplete toward the end.">
            <Chip size="small" label="truncated" color="warning" sx={{ ml: 1 }} />
          </Tooltip>
        )}
      </Box>

      <Box sx={{ px: 2, py: 1.5 }}>
        {topics?.length ? (
          <>
            <Typography variant="caption" color="text.secondary" fontWeight={600}>
              Discussed in this meeting
            </Typography>
            <Box component="ul" sx={{ listStyle: 'none', m: 0, mt: 0.75, p: 0 }}>
              {topics.slice(0, MAX_TOPICS).map((topic) => (
                <Box component="li" key={topic.title} sx={{ mb: 0.75 }}>
                  <Typography variant="body2" fontWeight={700} sx={{ color: 'text.primary' }}>
                    • {topic.title}
                  </Typography>
                  {topic.detail && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', pl: 1.5 }}>
                      {clip(topic.detail, 110)}
                    </Typography>
                  )}
                </Box>
              ))}
            </Box>
            {topics.length > MAX_TOPICS && (
              <Typography variant="caption" color="text.secondary">
                + {topics.length - MAX_TOPICS} more section{topics.length - MAX_TOPICS === 1 ? '' : 's'}
              </Typography>
            )}
          </>
        ) : (
          // Still loading, or a document with no ## sections to list — show the excerpt the
          // backend sent, clipped with a fade so it reads as a preview, not a cut-off document.
          <Box sx={{ position: 'relative', maxHeight: 190, overflow: 'hidden' }}>
            <Box sx={{ fontSize: '0.9rem' }}>
              <MarkdownRenderer content={minutes.preview} />
            </Box>
            <Box sx={{
              position: 'absolute', left: 0, right: 0, bottom: 0, height: 44,
              background: `linear-gradient(to bottom, ${alpha(tokens.card, 0)}, ${tokens.card})`,
              pointerEvents: 'none',
            }} />
          </Box>
        )}
      </Box>

      <Box sx={{ px: 2, pb: 1.5, display: 'flex', gap: 1, alignItems: 'center' }}>
        <Button
          size="small"
          variant="contained"
          startIcon={<OpenInNew />}
          onClick={() => navigate(`/minutes/${minutes.id}`)}
        >
          Open
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={downloading ? <CircularProgress size={16} /> : <Download />}
          onClick={handleDownload}
          disabled={downloading}
        >
          Download Word
        </Button>
        {error && <Typography variant="caption" color="error">{error}</Typography>}
      </Box>
    </Paper>
  );
}

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
    <Box sx={{ flexGrow: 1, overflowY: 'auto', px: { xs: 2, md: 4 }, py: 3 }}>
      {messages.map((message, index) => {
        const isUser = message.role === 'user';
        const time = formatTime(message.timestamp);
        return (
        <Box
          key={index}
          sx={{
            display: 'flex',
            justifyContent: isUser ? 'flex-end' : 'flex-start',
            mb: 2.5,
          }}
        >
          {/* Wrapper for vertical layout of bubble + time + suggestions */}
          <Box sx={{
            display: 'flex', flexDirection: 'column', minWidth: 0,
            maxWidth: { xs: '92%', md: isUser ? '70%' : 760 },
            alignItems: isUser ? 'flex-end' : 'flex-start',
          }}>
            <Box
              sx={{
                px: 2, py: 1.5,
                maxWidth: '100%',
                bgcolor: isUser ? 'secondary.light' : 'background.paper',
                color: 'text.primary',
                border: isUser ? 0 : 1,
                borderColor: 'divider',
                // Tail corner points at the speaker's side.
                borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                fontSize: { xs: '0.8438rem', xl: '0.875rem' },
              }}
            >
              {/* Render message content with markdown support for bot messages. A minutes reply
                  swaps the text for the document card — same information, plus the actions. While
                  the answer is still streaming there is no metadata yet, so it shows as text and
                  becomes a card once the response completes. */}
              {message.role === 'assistant' ? (
                message.context_metadata?.minutes
                  ? <MinutesCard minutes={message.context_metadata.minutes} />
                  : <CopyableAnswer content={message.content} />
              ) : (
                <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap', fontSize: { xs: '0.8438rem', xl: '0.875rem' } }}>
                  {message.content}
                </Typography>
              )}

              {/* Sources section removed per user request */}
            </Box>

            {time && (
              <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, px: 0.5 }}>
                {time}
              </Typography>
            )}

            {/* Suggestions Rendered Outside Bubble */}
            <Box sx={{ width: '100%', mt: 1 }}>
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
        );
      })}
      <div ref={messagesEndRef} />

      {/* Source Details Dialog */}
      <Dialog 
        open={Boolean(selectedSource)} 
        onClose={() => setSelectedSource(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 1, borderBottom: 1, borderColor: 'divider' }}>
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
          <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap', p: 2, bgcolor: tokens.sageSoft, borderRadius: 1 }}>
            {selectedSource?.text}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelectedSource(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export default MessageList;
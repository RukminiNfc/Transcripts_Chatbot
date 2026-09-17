import React, { useRef, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Paper, Typography, Chip, Tooltip, Dialog, DialogTitle, DialogContent, DialogActions, Button, IconButton, CircularProgress } from '@mui/material';
import { Person, SmartToy, Description, ContentCopy, Check, Download, OpenInNew } from '@mui/icons-material';
import MarkdownRenderer from './MarkdownRenderer';
import TopicSuggestions from './TopicSuggestions';
import { momAPI } from '../../services/api';

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
          sx={{ position: 'absolute', top: -4, right: -4, color: copied ? '#2e7d32' : '#6b7280' }}
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

// Markdown emphasis/links stripped back to plain text, for one-line topic labels.
const plain = (text) =>
  text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/[*_`]/g, '').trim();

const clip = (text, max) => (text.length > max ? `${text.slice(0, max).trimEnd()}…` : text);

/**
 * "What was discussed", derived from the document's own structure.
 *
 * The MOM prompt forbids summarizing ("omission is a defect"), so there is no overview paragraph
 * to preview — but its `##` sections ARE the meeting's topic list, one per feature/process area.
 * Each topic takes the first bullet beneath it as a one-line illustration. Nothing is invented:
 * every word here is lifted from the document.
 *
 * The two trailing sections are roll-ups of what came before, not topics in their own right.
 */
const APPENDIX_SECTIONS = /^(consolidated action items|team priorities)\b/i;

const deriveTopics = (markdown) => {
  const topics = [];
  let current = null;
  // `^##\s` cannot match a `###` line — the third character is '#', not whitespace.
  for (const line of (markdown || '').split('\n')) {
    const heading = /^##\s+(.+?)\s*$/.exec(line);
    if (heading) {
      const title = plain(heading[1]);
      current = !title || APPENDIX_SECTIONS.test(title) ? null : { title, detail: '' };
      if (current) topics.push(current);
      continue;
    }
    if (!current || current.detail) continue;
    const bullet = /^\s*[-*]\s+(.+?)\s*$/.exec(line);
    if (bullet) current.detail = plain(bullet[1]);
  }
  return topics;
};

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
    <Paper variant="outlined" sx={{ bgcolor: '#ffffff', borderRadius: 2, overflow: 'hidden' }}>
      <Box sx={{ px: 2, py: 1.25, bgcolor: '#e6f0ff', borderBottom: '1px solid #d6e4ff' }}>
        <Typography variant="subtitle2" fontWeight={700} sx={{ color: '#0d76ff' }}>
          📄 Minutes of Meeting
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
                  <Typography variant="body2" fontWeight={700} sx={{ color: '#1a2733' }}>
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
              background: 'linear-gradient(to bottom, rgba(255,255,255,0), #ffffff)',
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

              {/* Render message content with markdown support for bot messages. A minutes reply
                  swaps the text for the document card — same information, plus the actions. While
                  the answer is still streaming there is no metadata yet, so it shows as text and
                  becomes a card once the response completes. */}
              {message.role === 'assistant' ? (
                message.context_metadata?.minutes
                  ? <MinutesCard minutes={message.context_metadata.minutes} />
                  : <CopyableAnswer content={message.content} />
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
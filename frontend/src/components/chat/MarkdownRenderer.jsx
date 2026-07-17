import React from 'react';
import { Box } from '@mui/material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Markdown renderer backed by react-markdown + remark-gfm (GitHub-flavored markdown).
 * Renders numbered lists (1,2,3 — not 1,1,1), bullet lists, tables, code, and links
 * correctly, unlike the previous hand-rolled parser. Styling matches the app's blue theme.
 */
const BLUE = '#0d76ff';

const components = {
  h1: ({ node, ...p }) => (
    <Box component="h2" sx={{ fontWeight: 700, color: BLUE, fontSize: '1.25rem', mt: 2, mb: 1 }} {...p} />
  ),
  h2: ({ node, ...p }) => (
    <Box component="h2" sx={{ fontWeight: 700, color: BLUE, fontSize: '1.15rem', mt: 2, mb: 1 }} {...p} />
  ),
  h3: ({ node, ...p }) => (
    <Box component="h3" sx={{ fontWeight: 700, color: BLUE, fontSize: '1.02rem', mt: 1.5, mb: 0.5 }} {...p} />
  ),
  p: ({ node, ...p }) => <Box component="p" sx={{ my: 0.75, lineHeight: 1.6 }} {...p} />,
  strong: ({ node, ...p }) => <strong style={{ fontWeight: 700, color: BLUE }} {...p} />,
  ul: ({ node, ...p }) => <Box component="ul" sx={{ pl: 3, my: 0.75, '& li': { mb: 0.5 } }} {...p} />,
  ol: ({ node, ...p }) => <Box component="ol" sx={{ pl: 3, my: 0.75, '& li': { mb: 0.5 } }} {...p} />,
  li: ({ node, ...p }) => <Box component="li" sx={{ lineHeight: 1.6 }} {...p} />,
  a: ({ node, ...p }) => (
    <a style={{ color: BLUE, textDecoration: 'underline' }} target="_blank" rel="noopener noreferrer" {...p} />
  ),
  code: ({ node, inline, ...p }) =>
    inline ? (
      <Box component="code" sx={{ bgcolor: '#eef2f7', px: 0.5, borderRadius: 0.5, fontFamily: 'monospace', fontSize: '0.9em' }} {...p} />
    ) : (
      <Box component="code" sx={{ fontFamily: 'monospace', fontSize: '0.88em' }} {...p} />
    ),
  pre: ({ node, ...p }) => (
    <Box component="pre" sx={{ bgcolor: '#0f1720', color: '#e6edf3', p: 1.5, borderRadius: 1, overflowX: 'auto', my: 1 }} {...p} />
  ),
  // Wide tables must scroll inside their own container, not push the page sideways.
  table: ({ node, ...p }) => (
    <Box sx={{ overflowX: 'auto', my: 1 }}>
      <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%', '& th, & td': { border: '1px solid #d0d7de', p: 0.75, textAlign: 'left' }, '& th': { bgcolor: '#eef2f7', fontWeight: 700 } }} {...p} />
    </Box>
  ),
  blockquote: ({ node, ...p }) => (
    <Box component="blockquote" sx={{ borderLeft: `3px solid ${BLUE}`, pl: 1.5, my: 1, color: 'text.secondary' }} {...p} />
  ),
};

function MarkdownRenderer({ content }) {
  return (
    <Box sx={{ '& > *:first-of-type': { mt: 0 }, '& > *:last-child': { mb: 0 } }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content || ''}
      </ReactMarkdown>
    </Box>
  );
}

export default MarkdownRenderer;

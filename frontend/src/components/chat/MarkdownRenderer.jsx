import React from 'react';
import { Box, Typography } from '@mui/material';

/**
 * Simple markdown renderer for bot messages
 * Handles: **bold**, ### headings, - lists, numbered lists
 */
function MarkdownRenderer({ content }) {
    const renderContent = () => {
        const lines = content.split('\n');
        const elements = [];
        let listItems = [];
        let listType = null;

        const flushList = () => {
            if (listItems.length > 0) {
                elements.push(
                    <Box
                        key={`list-${elements.length}`}
                        component={listType === 'ordered' ? 'ol' : 'ul'}
                        sx={{
                            pl: 3,
                            my: 1,
                            '& li': { mb: 0.5 }
                        }}
                    >
                        {listItems.map((item, idx) => (
                            <li key={idx}>{renderInlineFormatting(item)}</li>
                        ))}
                    </Box>
                );
                listItems = [];
                listType = null;
            }
        };

        lines.forEach((line, index) => {
            const trimmedLine = line.trim();

            // Skip empty lines
            if (!trimmedLine) {
                flushList();
                elements.push(<Box key={`space-${index}`} sx={{ height: '8px' }} />);
                return;
            }

            // Heading (### or ##)
            if (trimmedLine.startsWith('###')) {
                flushList();
                const text = trimmedLine.replace(/^###\s*/, '');
                elements.push(
                    <Typography
                        key={`h3-${index}`}
                        variant="subtitle1"
                        sx={{ fontWeight: 700, mt: 1.5, mb: 0.5, color: '#ff6900' }}
                    >
                        {renderInlineFormatting(text)}
                    </Typography>
                );
            } else if (trimmedLine.startsWith('##')) {
                flushList();
                const text = trimmedLine.replace(/^##\s*/, '');
                elements.push(
                    <Typography
                        key={`h2-${index}`}
                        variant="h6"
                        sx={{ fontWeight: 700, mt: 2, mb: 1, color: '#ff6900' }}
                    >
                        {renderInlineFormatting(text)}
                    </Typography>
                );
            }
            // Unordered list item
            else if (trimmedLine.startsWith('- ')) {
                if (listType !== 'unordered') {
                    flushList();
                    listType = 'unordered';
                }
                listItems.push(trimmedLine.substring(2));
            }
            // Ordered list item (1. 2. etc)
            else if (/^\d+\.\s/.test(trimmedLine)) {
                if (listType !== 'ordered') {
                    flushList();
                    listType = 'ordered';
                }
                listItems.push(trimmedLine.replace(/^\d+\.\s/, ''));
            }
            // Regular paragraph
            else {
                flushList();
                elements.push(
                    <Typography
                        key={`p-${index}`}
                        variant="body1"
                        sx={{ mb: 0.5, lineHeight: 1.6 }}
                    >
                        {renderInlineFormatting(trimmedLine)}
                    </Typography>
                );
            }
        });

        // Flush any remaining list
        flushList();

        return elements;
    };

    const renderInlineFormatting = (text) => {
        const parts = [];
        let currentIndex = 0;
        let key = 0;

        // Match **bold** text
        const boldRegex = /\*\*([^*]+)\*\*/g;
        let match;

        while ((match = boldRegex.exec(text)) !== null) {
            // Add text before the match
            if (match.index > currentIndex) {
                parts.push(
                    <span key={`text-${key++}`}>
                        {text.substring(currentIndex, match.index)}
                    </span>
                );
            }

            // Add bold text
            parts.push(
                <strong key={`bold-${key++}`} style={{ fontWeight: 700, color: '#ff6900' }}>
                    {match[1]}
                </strong>
            );

            currentIndex = match.index + match[0].length;
        }

        // Add remaining text
        if (currentIndex < text.length) {
            parts.push(
                <span key={`text-${key++}`}>
                    {text.substring(currentIndex)}
                </span>
            );
        }

        return parts.length > 0 ? parts : text;
    };

    return <Box>{renderContent()}</Box>;
}

export default MarkdownRenderer;

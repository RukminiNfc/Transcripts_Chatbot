import React from 'react';
import { Box } from '@mui/material';
import './TypingIndicator.css';

function TypingIndicator() {
    return (
        <Box sx={{ display: 'flex', justifyContent: 'flex-start', mb: 2, px: { xs: 2, md: 4 } }}>
            <Box
                sx={{
                    px: 1, py: 0.5, bgcolor: 'background.paper', border: 1, borderColor: 'divider',
                    borderRadius: '16px 16px 16px 4px',
                }}
            >
                <Box className="typing-indicator" aria-label="Assistant is typing">
                    <span className="dot"></span>
                    <span className="dot"></span>
                    <span className="dot"></span>
                </Box>
            </Box>
        </Box>
    );
}

export default TypingIndicator;

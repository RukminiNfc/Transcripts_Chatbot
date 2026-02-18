import React from 'react';
import { Box, Paper } from '@mui/material';
import { SmartToy } from '@mui/icons-material';
import './TypingIndicator.css';

function TypingIndicator() {
    return (
        <Box
            sx={{
                display: 'flex',
                justifyContent: 'flex-start',
                mb: 2,
                px: 2,
            }}
        >
            <Paper
                elevation={2}
                sx={{
                    p: 2,
                    backgroundColor: '#f5f5f5',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                }}
            >
                <SmartToy sx={{ color: '#ff6900' }} />
                <Box className="typing-indicator">
                    <span className="dot"></span>
                    <span className="dot"></span>
                    <span className="dot"></span>
                </Box>
            </Paper>
        </Box>
    );
}

export default TypingIndicator;

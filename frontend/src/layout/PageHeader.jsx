import React from 'react';
import { Box, Typography } from '@mui/material';
import { tokens } from '../theme/theme';

/**
 * Standard page title block: icon tile, serif title, subtitle, and right-aligned actions.
 * Actions wrap under the title on narrow screens.
 */
export default function PageHeader({ icon, title, subtitle, actions }) {
  return (
    <Box
      sx={{
        display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 2,
        justifyContent: 'space-between', mb: 3,
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, minWidth: 0 }}>
        {icon && (
          <Box
            sx={{
              width: '3rem', height: '3rem', flexShrink: 0, borderRadius: 2, display: { xs: 'none', sm: 'grid' },
              placeItems: 'center', bgcolor: tokens.sageLight, color: 'primary.main',
            }}
          >
            {icon}
          </Box>
        )}
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="h2" component="h1" sx={{ fontSize: { xs: '1.5rem', md: '1.75rem' } }}>
            {title}
          </Typography>
          {subtitle && (
            <Typography variant="body2" color="text.secondary">{subtitle}</Typography>
          )}
        </Box>
      </Box>
      {actions && <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>{actions}</Box>}
    </Box>
  );
}

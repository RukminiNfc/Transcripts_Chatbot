import React from 'react';
import { Chip } from '@mui/material';
import { statusColors } from '../../theme/theme';

/**
 * Status pill for minutes and tasks. `status` is matched loosely (case-insensitive, and
 * "send failed" / "error" map to failed) so callers can pass the backend value as-is.
 */
const toKey = (status) => {
  const s = String(status || '').toLowerCase();
  if (s.includes('fail') || s.includes('error')) return 'failed';
  if (s.includes('sent')) return 'sent';
  if (s.includes('approv')) return 'approved';
  if (s.includes('draft') || s.includes('pending') || s.includes('generat')) return 'draft';
  return 'neutral';
};

export default function StatusChip({ status, label, size = 'small', sx }) {
  const c = statusColors[toKey(status)];
  return (
    <Chip
      size={size}
      label={label ?? status}
      sx={{ bgcolor: c.bg, color: c.fg, height: '1.5rem', ...sx }}
    />
  );
}

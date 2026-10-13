import React from 'react';
import { Chip } from '@mui/material';
import { categoryColors } from '../../theme/theme';

// Same category name -> same colour on every page and every load.
const hash = (s) => {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
};

export default function CategoryChip({ category, size = 'small', sx }) {
  const name = category || 'Uncategorised';
  const c = categoryColors[hash(name.toLowerCase()) % categoryColors.length];
  return (
    <Chip size={size} label={name} sx={{ bgcolor: c.bg, color: c.fg, height: '1.625rem', ...sx }} />
  );
}

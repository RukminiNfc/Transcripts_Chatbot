import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box, Paper, Typography, Button, Alert, CircularProgress, Divider, Snackbar
} from '@mui/material';
import { ArrowBack, Download } from '@mui/icons-material';
import MarkdownRenderer from '../chat/MarkdownRenderer';
import { momAPI } from '../../services/api';

/**
 * One meeting's minutes, rendered as a document.
 *
 * Deliberately reads the SAME stored content_markdown that the /download endpoint renders into
 * Word, so the screen and the file can never drift apart. No version number anywhere: the header
 * names the meeting, not the generation attempt.
 */
export default function MinutesDocument() {
  const { momId } = useParams();
  const navigate = useNavigate();

  const [mom, setMom] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'error' });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const data = await momAPI.get(momId);
        if (!cancelled) setMom(data);
      } catch (err) {
        if (!cancelled) setError(err.response?.data?.detail || 'Could not load these minutes.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [momId]);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await momAPI.download(mom.id, mom.session_name);
    } catch (err) {
      setSnackbar({ open: true, message: err.message || 'Download failed.', severity: 'error' });
    } finally {
      setDownloading(false);
    }
  };

  const callDate = mom?.call_date ? new Date(mom.call_date).toLocaleDateString(undefined, {
    day: 'numeric', month: 'long', year: 'numeric',
  }) : '';

  if (loading) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', p: 8 }}><CircularProgress /></Box>;
  }

  return (
    <Box sx={{ p: 3, maxWidth: 900, margin: '0 auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Button startIcon={<ArrowBack />} onClick={() => navigate('/minutes')}>
          Back to Minutes
        </Button>
        {mom?.content_markdown && (
          <Button
            variant="contained"
            startIcon={downloading ? <CircularProgress size={18} color="inherit" /> : <Download />}
            onClick={handleDownload}
            disabled={downloading}
          >
            Download Word
          </Button>
        )}
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      {mom && (
        <Paper elevation={2} sx={{ borderRadius: 2, p: { xs: 2, sm: 5 } }}>
          {/* Document header — mirrors the Word cover block built in document_render.py */}
          <Box sx={{ textAlign: 'center', mb: 3 }}>
            <img src="/nfclogo.jpg" alt="NFC Logo" style={{ height: 48, borderRadius: 4 }} />
            <Typography variant="h4" fontWeight="bold" sx={{ mt: 2, color: '#0d76ff' }}>
              Minutes of Meeting
            </Typography>
            <Typography variant="subtitle1" color="text.secondary" sx={{ mt: 0.5 }}>
              {mom.session_name}
            </Typography>
            {callDate && (
              <Typography variant="body2" color="text.secondary">{callDate}</Typography>
            )}
          </Box>

          <Divider sx={{ mb: 3 }} />

          {/* Same warning wording the Word document carries, so the two agree. */}
          {mom.truncated && (
            <Alert severity="warning" sx={{ mb: 3 }}>
              These minutes reached the generation length limit and may be incomplete toward the end.
            </Alert>
          )}

          {mom.content_markdown
            ? <MarkdownRenderer content={mom.content_markdown} />
            : <Alert severity="error">{mom.generation_error || 'No content.'}</Alert>}
        </Paper>
      )}

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity={snackbar.severity} sx={{ width: '100%' }}
               onClose={() => setSnackbar((s) => ({ ...s, open: false }))}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}

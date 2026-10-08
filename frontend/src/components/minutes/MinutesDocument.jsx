import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box, Paper, Typography, Button, Alert, CircularProgress, Divider, Snackbar,
  TextField, Tooltip
} from '@mui/material';
import { ArrowBack, Download, Edit, Save, Close, Visibility } from '@mui/icons-material';
import MarkdownRenderer from '../chat/MarkdownRenderer';
import { momAPI } from '../../services/api';
import { useAuth } from '../../auth/AuthContext';

/**
 * Count the structural markers in a document.
 *
 * Editing is for correcting words the transcription got wrong — not for restructuring. The
 * same markdown drives the Word export, so losing a heading silently changes the downloaded
 * document too. Comparing these before and after turns "only fix words" into something the
 * page can actually check, without blocking anyone who means it.
 */
const structureOf = (markdown) => {
  const lines = (markdown || '').split('\n');
  return {
    headings: lines.filter((l) => /^#{1,6}\s/.test(l.trim())).length,
    bullets: lines.filter((l) => /^\s*[-*+]\s/.test(l)).length,
  };
};

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

  const { isAdmin } = useAuth();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);

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

  const startEdit = () => {
    setDraft(mom.content_markdown || '');
    setPreview(false);
    setEditing(true);
  };

  const handleSave = async () => {
    if (!draft.trim()) {
      setSnackbar({ open: true, message: 'The document cannot be empty.', severity: 'error' });
      return;
    }

    // Structure guard. Editing is meant for wording; a changed heading or bullet count means
    // something structural moved, which also changes the Word export. Warn, do not block —
    // occasionally a real correction does remove a stray bullet.
    const before = structureOf(mom.content_markdown);
    const after = structureOf(draft);
    if (before.headings !== after.headings || before.bullets !== after.bullets) {
      const parts = [];
      if (before.headings !== after.headings) {
        parts.push(`headings ${before.headings} → ${after.headings}`);
      }
      if (before.bullets !== after.bullets) {
        parts.push(`bullets ${before.bullets} → ${after.bullets}`);
      }
      const ok = window.confirm(
        `This edit changes the document structure (${parts.join(', ')}).\n\n` +
        `That affects the Word download as well as this page.\n\nSave anyway?`
      );
      if (!ok) return;
    }

    setSaving(true);
    try {
      const updated = await momAPI.update(mom.id, draft);
      setMom(updated);
      setEditing(false);
      setSnackbar({ open: true, message: 'Minutes updated.', severity: 'success' });
    } catch (err) {
      setSnackbar({
        open: true,
        message: err.response?.data?.detail || err.message || 'Could not save.',
        severity: 'error',
      });
    } finally {
      setSaving(false);
    }
  };

  const callDate = mom?.call_date ? new Date(mom.call_date).toLocaleDateString(undefined, {
    day: 'numeric', month: 'long', year: 'numeric',
  }) : '';

  const editedAt = mom?.edited_at ? new Date(mom.edited_at).toLocaleString() : '';

  if (loading) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', p: 8 }}><CircularProgress /></Box>;
  }

  return (
    <Box sx={{ p: 3, maxWidth: 900, margin: '0 auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Button startIcon={<ArrowBack />} onClick={() => navigate('/minutes')} disabled={editing}>
          Back to Minutes
        </Button>

        <Box sx={{ display: 'flex', gap: 1 }}>
          {editing ? (
            <>
              <Button
                startIcon={<Visibility />}
                onClick={() => setPreview((p) => !p)}
                disabled={saving}
              >
                {preview ? 'Edit text' : 'Preview'}
              </Button>
              <Button startIcon={<Close />} onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </Button>
              <Button
                variant="contained"
                startIcon={saving ? <CircularProgress size={18} color="inherit" /> : <Save />}
                onClick={handleSave}
                disabled={saving}
              >
                Save
              </Button>
            </>
          ) : (
            <>
              {/* Editing changes what recipients receive, so it carries the same admin gate
                  as sending. Hidden rather than disabled, matching the Minutes list. */}
              {isAdmin && mom?.content_markdown && (
                <Tooltip title="Correct wording before sending">
                  <Button startIcon={<Edit />} onClick={startEdit}>Edit</Button>
                </Tooltip>
              )}
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
            </>
          )}
        </Box>
      </Box>

      {editing && mom?.status === 'sent' && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          These minutes have already been sent. Editing them here does not change what
          recipients received — send again if they need the corrected version.
        </Alert>
      )}

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

          {editedAt && !editing && (
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: 'block', mb: 2, fontStyle: 'italic' }}>
              Edited on {editedAt}
            </Typography>
          )}

          {editing ? (
            preview ? (
              // Preview renders the DRAFT, not the saved document, so the effect of an edit is
              // visible before it is committed — the same markdown also drives the Word export.
              <Box>
                <Alert severity="info" sx={{ mb: 2 }}>
                  Preview of your unsaved changes.
                </Alert>
                <MarkdownRenderer content={draft} />
              </Box>
            ) : (
              <TextField
                multiline
                fullWidth
                minRows={24}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck
                InputProps={{
                  sx: { fontFamily: 'Consolas, monospace', fontSize: '0.85rem', lineHeight: 1.6 },
                }}
                helperText="Correct wording only. Leave #, ## and - markers as they are — they shape the Word document."
              />
            )
          ) : mom.content_markdown
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

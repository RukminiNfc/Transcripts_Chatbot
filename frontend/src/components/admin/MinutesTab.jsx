import React, { useState, useEffect } from 'react';
import {
  Box, Typography, Button, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Chip, CircularProgress, Dialog, DialogTitle,
  DialogContent, DialogActions, Alert, Tooltip, IconButton
} from '@mui/material';
import { Refresh, Send, Visibility, Autorenew } from '@mui/icons-material';
import MarkdownRenderer from '../chat/MarkdownRenderer';

const API_URL = 'http://localhost:8001';

/**
 * Minutes of Meeting.
 *
 * Lists every transcript with the status of its latest generated minutes, so calls that have
 * NOT been generated yet are visible rather than simply absent. Generation is automatic on
 * upload; sending is deliberately manual — these go to a client-facing mailbox.
 */
export default function MinutesTab({ onNotify }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);       // transcript_id or mom id being worked on
  const [preview, setPreview] = useState(null);     // full MOM being viewed
  const [confirmSend, setConfirmSend] = useState(null);

  const notify = (message, severity = 'success') =>
    onNotify ? onNotify({ open: true, message, severity }) : null;

  useEffect(() => { load(); }, []);

  // Merge transcripts with their latest minutes so ungenerated calls still appear.
  const load = async (showLoader = true) => {
    try {
      if (showLoader) setLoading(true);
      const [tRes, mRes] = await Promise.all([
        fetch(`${API_URL}/requirements/transcripts`),
        fetch(`${API_URL}/api/mom/`),
      ]);
      const transcripts = tRes.ok ? await tRes.json() : [];
      const minutes = mRes.ok ? await mRes.json() : [];

      const byTranscript = {};
      minutes.forEach((m) => { byTranscript[m.transcript_id] = m; });

      const merged = (transcripts || []).map((t) => ({
        transcript_id: t.id,
        session_name: t.session_name,
        call_date: t.call_date,
        transcript_status: t.status,
        mom: byTranscript[t.id] || null,
      }));
      merged.sort((a, b) => new Date(b.call_date) - new Date(a.call_date));
      setRows(merged);
    } catch (err) {
      notify(`Could not load minutes: ${err.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const generate = async (transcriptId) => {
    setBusyId(transcriptId);
    try {
      const res = await fetch(`${API_URL}/api/mom/generate/${transcriptId}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Generation failed');
      if (data.generation_error) {
        notify(`Generated with an error: ${data.generation_error}`, 'error');
      } else if (data.truncated) {
        notify(`Minutes v${data.version} generated but TRUNCATED — the document is incomplete.`, 'warning');
      } else {
        notify(`Minutes v${data.version} generated.`);
      }
      await load(false);
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const view = async (momId) => {
    setBusyId(momId);
    try {
      const res = await fetch(`${API_URL}/api/mom/${momId}`);
      if (!res.ok) throw new Error('Could not load the document');
      setPreview(await res.json());
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const send = async (mom) => {
    setConfirmSend(null);
    setBusyId(mom.id);
    try {
      const res = await fetch(`${API_URL}/api/mom/${mom.id}/send`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Send failed');
      if (data.status === 'sent') {
        notify(`Sent to ${data.recipients.length} recipient(s).`);
      } else {
        notify(data.error || 'Send failed', 'error');
      }
      await load(false);
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const statusChip = (mom) => {
    if (!mom) return <Chip size="small" label="Not generated" variant="outlined" />;
    if (mom.generation_error) return <Chip size="small" label="Generation failed" color="error" />;
    if (mom.status === 'sent') return <Chip size="small" label="Sent" color="success" />;
    if (mom.status === 'send_failed') return <Chip size="small" label="Send failed" color="error" />;
    return <Chip size="small" label="Draft" color="warning" />;
  };

  if (loading) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}><CircularProgress /></Box>;
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="body2" color="text.secondary">
          Minutes are generated automatically after a transcript is processed. Review before sending —
          recipients come from the project's subscription list.
        </Typography>
        <Button size="small" startIcon={<Refresh />} onClick={() => load()}>Refresh</Button>
      </Box>

      <TableContainer>
        <Table>
          <TableHead sx={{ backgroundColor: '#f5f5f5' }}>
            <TableRow>
              <TableCell><b>Session</b></TableCell>
              <TableCell><b>Call Date</b></TableCell>
              <TableCell><b>Version</b></TableCell>
              <TableCell><b>Status</b></TableCell>
              <TableCell><b>Sent At</b></TableCell>
              <TableCell align="center"><b>Actions</b></TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                  No transcripts yet.
                </TableCell>
              </TableRow>
            )}
            {rows.map((row) => {
              const mom = row.mom;
              const working = busyId === row.transcript_id || (mom && busyId === mom.id);
              return (
                <TableRow key={row.transcript_id} hover>
                  <TableCell>{row.session_name}</TableCell>
                  <TableCell>{new Date(row.call_date).toLocaleDateString()}</TableCell>
                  <TableCell>
                    {mom ? `v${mom.version}` : '—'}
                    {mom?.truncated && (
                      <Tooltip title="The document hit the generation length limit and may be incomplete.">
                        <Chip size="small" label="truncated" color="warning" sx={{ ml: 1 }} />
                      </Tooltip>
                    )}
                  </TableCell>
                  <TableCell>{statusChip(mom)}</TableCell>
                  <TableCell>
                    {mom?.email_sent_at ? new Date(mom.email_sent_at).toLocaleString() : '—'}
                  </TableCell>
                  <TableCell align="center">
                    {working ? <CircularProgress size={20} /> : (
                      <>
                        {mom && !mom.generation_error && (
                          <Tooltip title="View">
                            <IconButton size="small" onClick={() => view(mom.id)}>
                              <Visibility fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        <Tooltip title={mom ? 'Regenerate as a new version' : 'Generate minutes'}>
                          <IconButton size="small" onClick={() => generate(row.transcript_id)}>
                            <Autorenew fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        {mom && !mom.generation_error && (
                          <Tooltip title="Send by email">
                            <IconButton size="small" color="primary" onClick={() => setConfirmSend(mom)}>
                              <Send fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                      </>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>

      {/* ── Preview */}
      <Dialog open={!!preview} onClose={() => setPreview(null)} maxWidth="md" fullWidth>
        <DialogTitle>
          {preview?.session_name} — v{preview?.version}
          <Typography variant="caption" display="block" color="text.secondary">
            Generated by {preview?.model_used}
          </Typography>
        </DialogTitle>
        <DialogContent dividers>
          {preview?.truncated && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              These minutes reached the generation length limit and may be incomplete toward the end.
            </Alert>
          )}
          {preview?.content_markdown
            ? <MarkdownRenderer content={preview.content_markdown} />
            : <Alert severity="error">{preview?.generation_error || 'No content.'}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPreview(null)}>Close</Button>
          {preview?.content_markdown && (
            <Button variant="contained" startIcon={<Send />}
                    onClick={() => { setConfirmSend(preview); setPreview(null); }}>
              Send
            </Button>
          )}
        </DialogActions>
      </Dialog>

      {/* ── Send confirmation. Deliberate friction: this leaves the building. */}
      <Dialog open={!!confirmSend} onClose={() => setConfirmSend(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Send these minutes?</DialogTitle>
        <DialogContent>
          <Typography gutterBottom>
            <b>{confirmSend?.session_name}</b> — version {confirmSend?.version}
          </Typography>
          <Alert severity="info" sx={{ mt: 1 }}>
            This emails everyone on the project's active subscription list. Manage recipients via
            the subscriptions API.
          </Alert>
          {confirmSend?.truncated && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              This document is marked truncated and may be incomplete. Consider regenerating first.
            </Alert>
          )}
          {confirmSend?.status === 'sent' && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              These minutes were already sent
              {confirmSend?.email_sent_at ? ` on ${new Date(confirmSend.email_sent_at).toLocaleString()}` : ''}.
              Sending again will deliver a duplicate.
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmSend(null)}>Cancel</Button>
          <Button variant="contained" color="primary" onClick={() => send(confirmSend)}>
            Send
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

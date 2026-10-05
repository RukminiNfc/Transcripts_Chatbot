import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, Paper, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Chip, CircularProgress, Dialog, DialogTitle,
  DialogContent, DialogActions, Alert, Tooltip, IconButton, Snackbar
} from '@mui/material';
import { Refresh, Send, Visibility, Autorenew, Download, Description, PlaylistAddCheck } from '@mui/icons-material';
import { momAPI } from '../../services/api';
import { useAuth } from '../../auth/AuthContext';

/**
 * Minutes of Meeting.
 *
 * Its own page rather than an Admin tab: any logged-in user may read and download minutes, while
 * generating (costs money) and sending (reaches a client mailbox) stay admin-only — matching the
 * split permissions on /api/mom.
 *
 * Admins also see meetings with NO minutes yet, merged in from /requirements/transcripts. That
 * endpoint is admin-only, so for a normal user the list simply shows the meetings that HAVE
 * minutes — there is nothing they could do about an ungenerated one anyway.
 *
 * No version column: regenerating produces a new row for audit, but the UI always shows the
 * latest, and a version number here would be confused with REQUIREMENT versions, which are a
 * different concept entirely.
 */
export default function MinutesPage() {
  const { isAdmin } = useAuth();
  const navigate = useNavigate();

  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);       // transcript_id or mom id being worked on
  const [confirmSend, setConfirmSend] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

  const notify = (message, severity = 'success') => setSnackbar({ open: true, message, severity });

  useEffect(() => { load(); }, [isAdmin]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = async (showLoader = true) => {
    try {
      if (showLoader) setLoading(true);

      // Admins get the full meeting list (including ungenerated); everyone else gets the minutes.
      const [minutes, transcripts] = await Promise.all([
        momAPI.list(),
        isAdmin ? momAPI.listTranscripts().catch(() => []) : Promise.resolve(null),
      ]);

      let merged;
      if (transcripts) {
        const byTranscript = {};
        (minutes || []).forEach((m) => { byTranscript[m.transcript_id] = m; });
        merged = (transcripts || []).map((t) => ({
          transcript_id: t.id,
          session_name: t.session_name,
          call_date: t.call_date,
          mom: byTranscript[t.id] || null,
        }));
      } else {
        merged = (minutes || []).map((m) => ({
          transcript_id: m.transcript_id,
          session_name: m.session_name,
          call_date: m.call_date,
          mom: m,
        }));
      }

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
      const data = await momAPI.generate(transcriptId);
      if (data.generation_error) {
        notify(`Generated with an error: ${data.generation_error}`, 'error');
      } else if (data.truncated) {
        notify('Minutes generated but TRUNCATED — the document is incomplete.', 'warning');
      } else {
        notify('Minutes generated.');
      }
      await load(false);
    } catch (err) {
      notify(err.response?.data?.detail || err.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const download = async (mom) => {
    setBusyId(mom.id);
    try {
      await momAPI.download(mom.id, mom.session_name);
    } catch (err) {
      notify(err.message || 'Download failed.', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const send = async (mom) => {
    setConfirmSend(null);
    setBusyId(mom.id);
    try {
      const data = await momAPI.send(mom.id);
      if (data.status === 'sent') {
        notify(`Sent to ${data.recipients.length} recipient(s).`);
      } else {
        notify(data.error || 'Send failed', 'error');
      }
      await load(false);
    } catch (err) {
      notify(err.response?.data?.detail || err.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const truncatedChip = (
    <Tooltip title="The document hit the generation length limit and may be incomplete.">
      <Chip size="small" label="truncated" color="warning" sx={{ ml: 1 }} />
    </Tooltip>
  );

  const statusChip = (mom) => {
    if (!mom) return <Chip size="small" label="Not generated" variant="outlined" />;
    if (mom.generation_error) return <Chip size="small" label="Generation failed" color="error" />;
    if (mom.status === 'sent') return <Chip size="small" label="Sent" color="success" />;
    if (mom.status === 'send_failed') return <Chip size="small" label="Send failed" color="error" />;
    return <Chip size="small" label="Draft" color="warning" />;
  };

  return (
    <Box sx={{ p: 3, maxWidth: 1200, margin: '0 auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h4" fontWeight="bold">
          <Description sx={{ mr: 1, verticalAlign: 'text-bottom', color: '#0d76ff' }} />
          Minutes of Meeting
        </Typography>
        <Button size="small" startIcon={<Refresh />} onClick={() => load()}>Refresh</Button>
      </Box>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {isAdmin
          ? `Minutes are generated automatically after a transcript is processed. Review before
             sending — recipients come from the project's subscription list.`
          : 'Open a meeting to read its minutes, or download them as a Word document.'}
      </Typography>

      <Paper elevation={2} sx={{ borderRadius: 2 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}><CircularProgress /></Box>
        ) : (
          <TableContainer>
            <Table>
              <TableHead sx={{ backgroundColor: '#f5f5f5' }}>
                <TableRow>
                  <TableCell><b>Meeting</b></TableCell>
                  <TableCell><b>Date</b></TableCell>
                  {/* Status tracks generating and sending, both admin actions. For a normal user
                      every row here already HAS minutes, so the column only ever reads "Sent" or
                      "Draft" — noise against a document they can simply open. */}
                  {isAdmin && <TableCell><b>Status</b></TableCell>}
                  {/* When the minutes were emailed is an admin concern — sending is their action
                      and only they can repeat it. A reader just opens the document. */}
                  {isAdmin && <TableCell><b>Sent</b></TableCell>}
                  <TableCell align="center"><b>Actions</b></TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={isAdmin ? 5 : 3} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                      {isAdmin ? 'No transcripts yet.' : 'No minutes are available yet.'}
                    </TableCell>
                  </TableRow>
                )}
                {rows.map((row) => {
                  const mom = row.mom;
                  const readable = mom && !mom.generation_error;
                  const working = busyId === row.transcript_id || (mom && busyId === mom.id);
                  return (
                    <TableRow
                      key={row.transcript_id || mom?.id}
                      hover
                      // The whole row opens the document, as the spec asks. Only when there is
                      // something to open — an ungenerated row stays inert.
                      onClick={readable ? () => navigate(`/minutes/${mom.id}`) : undefined}
                      sx={{ cursor: readable ? 'pointer' : 'default' }}
                    >
                      <TableCell>
                        {row.session_name}
                        {/* Without the Status column, the incomplete-document warning has to ride
                            along with the meeting name — it is the one part of that column a
                            reader genuinely needs. */}
                        {!isAdmin && mom?.truncated && truncatedChip}
                      </TableCell>
                      <TableCell>
                        {row.call_date ? new Date(row.call_date).toLocaleDateString() : '—'}
                      </TableCell>
                      {isAdmin && (
                        <TableCell>
                          {statusChip(mom)}
                          {mom?.truncated && truncatedChip}
                          {/* Approval is separate from the email status: a MOM can be approved
                              (tasks in Azure Boards) and still be a draft email, or vice versa. */}
                          {mom?.approved_at && (
                            <Tooltip title={`Approved by ${mom.approved_by} on ${new Date(mom.approved_at).toLocaleString()}`}>
                              <Chip size="small" label="Approved" color="success" variant="outlined" sx={{ ml: 1 }} />
                            </Tooltip>
                          )}
                        </TableCell>
                      )}
                      {isAdmin && (
                        <TableCell>
                          {mom?.email_sent_at ? new Date(mom.email_sent_at).toLocaleString() : '—'}
                        </TableCell>
                      )}
                      {/* Row-level actions must not also trigger the row's open-document click. */}
                      <TableCell align="center" onClick={(e) => e.stopPropagation()}>
                        {working ? <CircularProgress size={20} /> : (
                          <>
                            {readable && (
                              <>
                                <Tooltip title="Open">
                                  <IconButton size="small" onClick={() => navigate(`/minutes/${mom.id}`)}>
                                    <Visibility fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                                <Tooltip title="Download Word">
                                  <IconButton size="small" onClick={() => download(mom)}>
                                    <Download fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              </>
                            )}

                            {/* Generate + Send return 403 for non-admins — hide, don't disable. */}
                            {isAdmin && row.transcript_id && (
                              <Tooltip title={mom ? 'Regenerate' : 'Generate minutes'}>
                                <IconButton size="small" onClick={() => generate(row.transcript_id)}>
                                  <Autorenew fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            {isAdmin && readable && (
                              <Tooltip title={mom.approved_at ? 'View tasks' : 'Review tasks & approve'}>
                                <IconButton size="small" color="primary" onClick={() => navigate(`/minutes/${mom.id}/tasks`)}>
                                  <PlaylistAddCheck fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            {isAdmin && readable && (
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
        )}
      </Paper>

      {/* ── Send confirmation. Deliberate friction: this leaves the building. */}
      <Dialog open={!!confirmSend} onClose={() => setConfirmSend(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Send these minutes?</DialogTitle>
        <DialogContent>
          <Typography gutterBottom><b>{confirmSend?.session_name}</b></Typography>
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

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
          severity={snackbar.severity}
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}

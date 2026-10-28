import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, Paper, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, CircularProgress, Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Alert, IconButton, Tooltip, Snackbar, Skeleton, useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { CloudUploadOutlined, DeleteForever, UploadFileOutlined } from '@mui/icons-material';
import { useProject } from '../../projects/ProjectContext';
import PageHeader from '../../layout/PageHeader';
import StatusChip from '../common/StatusChip';
import { tokens } from '../../theme/theme';
import { API_ORIGIN as API_URL } from '../../services/api';

// Moved out of the old Admin page (Admin → Transcripts). Raw fetch is kept as it was: the global
// fetch wrapper in main.jsx attaches the JWT to every call to the backend.

// ─── Comparison status ────────────────────────────────────────────────────────
/**
 * Comparison runs separately from ingestion, so "processed" says only that the transcript was
 * parsed and its requirements extracted — NOT that they have been reconciled against history.
 * Without this column a transcript still awaiting comparison looks identical to a finished one.
 *
 * The staged count rides along with "Pending" because that is the number that matters when
 * deciding whether to run a backfill: how much work is queued, not merely how many transcripts.
 * Transcripts uploaded before staging existed report 0 and just show "Pending".
 */
function ComparisonChip({ status, staged }) {
  if (!status) return <Typography variant="body2" color="text.disabled">—</Typography>;

  const variants = {
    pending:   { label: staged > 0 ? `Pending (${staged})` : 'Pending', status: 'draft' },
    comparing: { label: 'Comparing…', status: 'sent' },
    compared:  { label: 'Compared', status: 'approved' },
    failed:    { label: 'Failed', status: 'failed' },
  };
  const v = variants[status] || { label: status, status: 'neutral' };
  return <StatusChip status={v.status} label={v.label} />;
}

// Processing status of the upload itself (parse + extract), mapped onto the shared chip colours.
const processingChip = (status) => {
  const map = { processed: 'approved', processing: 'sent', failed: 'failed' };
  return <StatusChip status={map[status] || 'neutral'} label={status || '—'} />;
};

const fmtDate = (v) => (v ? new Date(v).toLocaleDateString() : '—');
const fmtDateTime = (v) => (v ? new Date(v).toLocaleString() : '—');

export default function TranscriptsPage() {
  const { projectId, project, projects } = useProject();
  const navigate = useNavigate();
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));

  const [transcripts, setTranscripts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);   // transcript row
  const [deletingId, setDeletingId] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

  // Backlog awaiting comparison. Derived from the list already fetched — no extra request, and
  // it cannot drift out of step with the rows shown in the table.
  const pendingComparison = useMemo(() => {
    const waiting = transcripts.filter((t) => t.comparison_status === 'pending');
    return {
      count: waiting.length,
      staged: waiting.reduce((sum, t) => sum + (t.requirements_staged || 0), 0),
    };
  }, [transcripts]);

  useEffect(() => {
    if (projectId) fetchTranscripts();
    else setTranscripts([]);
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchTranscripts = async (showLoader = true) => {
    if (!projectId) return;
    try {
      if (showLoader) setLoading(true);
      const res = await fetch(`${API_URL}/requirements/transcripts?customer_id=${encodeURIComponent(projectId)}`);
      if (res.ok) setTranscripts(await res.json());
    } catch (e) {
      console.error('Failed to fetch transcripts:', e);
    } finally {
      if (showLoader) setLoading(false);
    }
  };

  // Poll for status updates if any transcript is still processing
  useEffect(() => {
    const isProcessing = transcripts.some((t) => t.status === 'processing');
    let interval;
    if (isProcessing) {
      interval = setInterval(() => {
        fetchTranscripts(false); // fetch quietly without replacing the table with a skeleton
      }, 5000);
    }
    return () => clearInterval(interval);
  }, [transcripts]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDelete = async (t) => {
    setConfirmDelete(null);
    setDeletingId(t.id);
    try {
      const res = await fetch(`${API_URL}/requirements/transcript/${t.id}`, { method: 'DELETE' });
      const data = await res.json();
      if (res.ok) {
        setSnackbar({ open: true, message: `"${t.session_name}" deleted successfully.`, severity: 'success' });
        fetchTranscripts();
      } else {
        setSnackbar({ open: true, message: data.detail || 'Delete failed.', severity: 'error' });
      }
    } catch {
      setSnackbar({ open: true, message: 'Network error. Please try again.', severity: 'error' });
    } finally {
      setDeletingId(null);
    }
  };

  const deleteButton = (t) => (
    <Tooltip title="Delete this transcript and all its data">
      <span>
        <IconButton
          color="error"
          onClick={() => setConfirmDelete(t)}
          disabled={deletingId === t.id}
          aria-label={`Delete ${t.session_name}`}
        >
          {deletingId === t.id ? <CircularProgress size={18} color="error" /> : <DeleteForever fontSize="small" />}
        </IconButton>
      </span>
    </Tooltip>
  );

  return (
    <Box sx={{ px: { xs: 2, md: 3 }, py: { xs: 2, md: 3 } }}>
      <PageHeader
        icon={<UploadFileOutlined />}
        title="Transcripts"
        subtitle={project
          ? `Upload grooming-call transcripts for ${project.name} and track their processing.`
          : 'Upload grooming-call transcripts and track their processing.'}
        actions={(
          <Tooltip title={project ? '' : 'Create a project in Project Configuration first'}>
            <span>
              <Button variant="contained" startIcon={<CloudUploadOutlined />} onClick={() => setUploadOpen(true)} disabled={!project}>
                Upload transcript
              </Button>
            </span>
          </Tooltip>
        )}
      />

      {!project && projects.length === 0 && (
        <Alert
          severity="warning"
          sx={{ mb: 2 }}
          action={<Button color="inherit" size="small" onClick={() => navigate('/settings/projects')}>Create project</Button>}
        >
          No project configured yet. Create one in Project Configuration before uploading transcripts.
        </Alert>
      )}

      {/* Backlog summary. Only shown when something is actually waiting, so a fully
          reconciled project carries no standing warning. The requirement total is the
          figure worth seeing: 12 transcripts could be 50 requirements or 1,000. */}
      {!loading && pendingComparison.count > 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <strong>{pendingComparison.count}</strong>
          {pendingComparison.count === 1 ? ' transcript is' : ' transcripts are'} awaiting comparison
          {pendingComparison.staged > 0 && <> — <strong>{pendingComparison.staged}</strong> requirements staged</>}
          . Minutes and transcript search work normally; requirement counts and change history
          exclude these until comparison runs.
        </Alert>
      )}

      {loading ? (
        [0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={56} sx={{ mb: 1 }} />)
      ) : transcripts.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 5, textAlign: 'center', color: 'text.secondary', borderRadius: 3 }}>
          {project ? `No transcripts uploaded yet for ${project.name}.` : 'No transcripts.'}
        </Paper>
      ) : isDesktop ? (
        <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 3 }}>
          <Table size="small" sx={{ '& td': { py: 1.25 } }}>
            <TableHead>
              <TableRow>
                <TableCell>Session</TableCell>
                <TableCell>File</TableCell>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>Call Date</TableCell>
                <TableCell>Blocks</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Comparison</TableCell>
                <TableCell>Uploaded</TableCell>
                <TableCell sx={{ width: '4rem' }} aria-label="Actions" />
              </TableRow>
            </TableHead>
            <TableBody>
              {transcripts.map((t) => (
                <TableRow key={t.id} hover>
                  <TableCell sx={{ fontWeight: 600 }}>{t.session_name}</TableCell>
                  <TableCell sx={{ color: 'text.secondary', wordBreak: 'break-all' }}>{t.filename}</TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmtDate(t.call_date)}</TableCell>
                  <TableCell>{t.total_blocks}</TableCell>
                  <TableCell>{processingChip(t.status)}</TableCell>
                  <TableCell><ComparisonChip status={t.comparison_status} staged={t.requirements_staged} /></TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap', color: 'text.secondary' }}>{fmtDateTime(t.upload_date)}</TableCell>
                  <TableCell align="right">{deleteButton(t)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      ) : (
        transcripts.map((t) => (
          <Paper key={t.id} variant="outlined" sx={{ p: 2, mb: 1.5, borderRadius: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography sx={{ fontWeight: 600 }}>{t.session_name}</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ wordBreak: 'break-all' }}>{t.filename}</Typography>
              </Box>
              {deleteButton(t)}
            </Box>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 1 }}>
              {processingChip(t.status)}
              <ComparisonChip status={t.comparison_status} staged={t.requirements_staged} />
            </Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              Call {fmtDate(t.call_date)} · {t.total_blocks ?? 0} blocks · Uploaded {fmtDateTime(t.upload_date)}
            </Typography>
          </Paper>
        ))
      )}

      {/* Delete confirmation (was window.confirm) */}
      <Dialog open={!!confirmDelete} onClose={() => setConfirmDelete(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Delete “{confirmDelete?.session_name}”?</DialogTitle>
        <DialogContent>
          <Typography>
            This will permanently remove all conversation logs, requirement versions, and Qdrant
            vectors for this grooming call.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={() => handleDelete(confirmDelete)}>Delete</Button>
        </DialogActions>
      </Dialog>

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onSuccess={() => { setUploadOpen(false); fetchTranscripts(); }}
        customerId={project?.id}
        customerName={project?.name}
      />

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert onClose={() => setSnackbar((s) => ({ ...s, open: false }))} severity={snackbar.severity} sx={{ width: '100%' }}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}

// ─── Upload Transcript Dialog ─────────────────────────────────────────────────
function UploadDialog({ open, onClose, onSuccess, customerId, customerName }) {
  const [file, setFile] = useState(null);
  const [sessionName, setSessionName] = useState('');
  const [callDate, setCallDate] = useState(new Date().toISOString().split('T')[0]);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState(null);

  const handleUpload = async () => {
    if (!file || !sessionName || !customerId) return;
    setUploading(true);
    setResult(null);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('customer_id', customerId);
    formData.append('session_name', sessionName);
    formData.append('call_date', new Date(callDate).toISOString());

    try {
      const response = await fetch(`${API_URL}/requirements/transcript`, {
        method: 'POST',
        body: formData,
      });
      const data = await response.json();

      if (!response.ok) {
        // FastAPI errors arrive as { detail }, not { message } — without this the box renders empty.
        setResult({ message: data.detail || data.message || `Upload failed (HTTP ${response.status}).` });
        setUploading(false);
        return;
      }

      // If the backend returned a transcript_id, start polling the status
      if (data.transcript_id) {
        setResult({ message: 'Uploading & Processing in background...' });

        const pollStatus = async (transcriptId) => {
          try {
            const statusRes = await fetch(`${API_URL}/requirements/task/${transcriptId}`);
            const statusData = await statusRes.json();

            if (statusData.status === 'processing') {
              // Still processing, check again in 3 seconds
              setTimeout(() => pollStatus(transcriptId), 3000);
            } else if (statusData.status === 'processed') {
              setResult({
                message: 'Transcript processed successfully',
                summary: statusData.summary,
              });
              setUploading(false);
              setTimeout(onSuccess, 2500);
            } else if (statusData.status === 'failed') {
              setResult({ message: 'Processing failed in the background.' });
              setUploading(false);
            }
          } catch (e) {
            console.error('Polling error', e);
            // If network blips, keep trying
            setTimeout(() => pollStatus(transcriptId), 3000);
          }
        };

        pollStatus(data.transcript_id);
      } else {
        // Fallback if no transcript_id is returned
        setResult(data);
        setUploading(false);
        if (response.ok) setTimeout(onSuccess, 2500);
      }
    } catch (error) {
      console.error('Upload failed:', error);
      setResult({ message: 'Upload failed due to network error.' });
      setUploading(false);
    }
  };

  return (
    <Dialog open={open} onClose={uploading ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        Upload grooming transcript
        {customerName && (
          <Typography variant="caption" display="block" color="text.secondary">
            Project: {customerName}
          </Typography>
        )}
      </DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3, mt: 1 }}>
          <TextField
            label="Session Name (e.g. Grooming 35)"
            value={sessionName}
            onChange={(e) => setSessionName(e.target.value)}
            fullWidth
            required
          />
          <TextField
            label="Call Date"
            type="date"
            value={callDate}
            onChange={(e) => setCallDate(e.target.value)}
            fullWidth
            slotProps={{ inputLabel: { shrink: true } }}
            required
          />
          <Button variant="outlined" component="label" fullWidth startIcon={<UploadFileOutlined />}>
            {file ? file.name : 'Select DOCX transcript'}
            <input type="file" hidden accept=".docx" onChange={(e) => setFile(e.target.files[0])} />
          </Button>

          {uploading && (
            <Box sx={{ textAlign: 'center', my: 2 }}>
              <CircularProgress size={24} sx={{ mb: 1 }} />
              <Typography variant="body2" color="text.secondary">
                Parsing transcript, extracting requirements via AI, and checking diffs... This may take a minute.
              </Typography>
            </Box>
          )}

          {result && (
            <Alert severity={result.summary ? 'success' : uploading ? 'info' : 'error'} icon={false} sx={{ bgcolor: result.summary ? tokens.sageSoft : undefined }}>
              <Typography variant="subtitle2">{result.message}</Typography>
              {result.summary && (
                <Typography variant="body2" sx={{ mt: 1 }}>
                  Extracted: {result.summary.total_extracted} | Added: {result.summary.added} | Modified: {result.summary.modified}
                </Typography>
              )}
            </Alert>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={uploading}>Cancel</Button>
        <Button onClick={handleUpload} variant="contained" disabled={uploading || !file || !sessionName}>
          Upload & Process
        </Button>
      </DialogActions>
    </Dialog>
  );
}

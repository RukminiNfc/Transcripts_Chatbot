import React, { useState, useEffect } from 'react';
import {
  Box, Typography, Button, Paper, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Chip, CircularProgress,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Tabs, Tab, Alert, IconButton, Tooltip, Divider, Card, CardContent,
  CardActions, Snackbar
} from '@mui/material';
import {
  CloudUpload, Settings, Add, Edit, Delete, CheckCircle, Business,
  DeleteForever
} from '@mui/icons-material';

const API_URL = 'http://localhost:8001';

// ─── Tab Panel Helper ─────────────────────────────────────────────────────────
function TabPanel({ children, value, index }) {
  return value === index ? <Box sx={{ pt: 3 }}>{children}</Box> : null;
}

// ─── Main Admin Dashboard ─────────────────────────────────────────────────────
export default function AdminDashboard() {
  const [activeTab, setActiveTab] = useState(0);
  const [transcripts, setTranscripts] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [activeCustomer, setActiveCustomer] = useState(null);
  const [loadingTranscripts, setLoadingTranscripts] = useState(false);
  const [loadingCustomers, setLoadingCustomers] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [customerFormOpen, setCustomerFormOpen] = useState(false);
  const [editingCustomer, setEditingCustomer] = useState(null);
  const [deleteError, setDeleteError] = useState(''); // ← holds blocking error message
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });
  const [deletingTranscriptId, setDeletingTranscriptId] = useState(null);

  // ── Load data on mount
  useEffect(() => {
    fetchTranscripts();
    fetchCustomers();
  }, []);

  const fetchTranscripts = async (showLoader = true) => {
    try {
      if (showLoader) setLoadingTranscripts(true);
      const res = await fetch(`${API_URL}/requirements/transcripts`);
      if (res.ok) setTranscripts(await res.json());
    } catch (e) {
      console.error('Failed to fetch transcripts:', e);
    } finally {
      if (showLoader) setLoadingTranscripts(false);
    }
  };

  // Poll for status updates if any transcript is still processing
  useEffect(() => {
    const isProcessing = transcripts.some(t => t.status === 'processing');
    let interval;
    if (isProcessing) {
      interval = setInterval(() => {
        fetchTranscripts(false); // fetch quietly without replacing the table with a spinner
      }, 5000);
    }
    return () => clearInterval(interval);
  }, [transcripts]);

  const fetchCustomers = async () => {
    try {
      setLoadingCustomers(true);
      const res = await fetch(`${API_URL}/requirements/customers`);
      if (res.ok) {
        const data = await res.json();
        setCustomers(data);
        // Auto-select first customer as active
        if (data.length > 0 && !activeCustomer) setActiveCustomer(data[0]);
      }
    } catch (e) {
      console.error('Failed to fetch customers:', e);
    } finally {
      setLoadingCustomers(false);
    }
  };

  const handleDeleteTranscript = async (transcriptId, sessionName) => {
    if (!window.confirm(
      `Delete "${sessionName}"?\n\nThis will permanently remove all conversation logs, requirement versions, and Qdrant vectors for this grooming call.`
    )) return;

    setDeletingTranscriptId(transcriptId);
    try {
      const res = await fetch(`${API_URL}/requirements/transcript/${transcriptId}`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (res.ok) {
        setSnackbar({ open: true, message: `"${sessionName}" deleted successfully.`, severity: 'success' });
        fetchTranscripts();
      } else {
        setSnackbar({ open: true, message: data.detail || 'Delete failed.', severity: 'error' });
      }
    } catch (e) {
      setSnackbar({ open: true, message: 'Network error. Please try again.', severity: 'error' });
    } finally {
      setDeletingTranscriptId(null);
    }
  };

  const handleDeleteCustomer = async (customerId) => {
    if (!window.confirm('Are you sure you want to delete this project?')) return;
    setDeleteError('');
    try {
      const res = await fetch(`${API_URL}/requirements/customer/${customerId}`, { method: 'DELETE' });
      if (!res.ok) {
        const data = await res.json();
        // Show the backend's descriptive blocking message
        setDeleteError(data.detail || 'Could not delete project.');
        return;
      }
      fetchCustomers();
      if (activeCustomer?.id === customerId) setActiveCustomer(null);
    } catch (e) {
      setDeleteError('Network error. Please try again.');
    }
  };

  return (
    <Box sx={{ p: 3, maxWidth: 1200, margin: '0 auto' }}>
      {/* ── Page Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h4" fontWeight="bold">Admin Panel</Typography>

        {activeTab === 0 && (
          <Button
            variant="contained"
            color="primary"
            startIcon={<CloudUpload />}
            onClick={() => setUploadOpen(true)}
            disabled={!activeCustomer}
          >
            {activeCustomer ? 'Upload New Transcript' : 'Set up a Project first'}
          </Button>
        )}

        {activeTab === 1 && (
          <Button
            variant="contained"
            color="primary"
            startIcon={<Add />}
            onClick={() => { setEditingCustomer(null); setCustomerFormOpen(true); }}
          >
            New Project
          </Button>
        )}
      </Box>

      {/* ── Active Customer Badge */}
      {activeCustomer && (
        <Alert
          severity="success"
          icon={<CheckCircle />}
          sx={{ mb: 2, borderRadius: 2 }}
        >
          <strong>Active Project:</strong> {activeCustomer.name} &nbsp;|&nbsp;
          <strong>Client Speaker:</strong> {activeCustomer.client_speaker_name}
        </Alert>
      )}

      {!activeCustomer && (
        <Alert severity="warning" sx={{ mb: 2, borderRadius: 2 }}>
          No project configured yet. Go to the <strong>Customer Settings</strong> tab to create one before uploading transcripts.
        </Alert>
      )}

      {/* ── Tabs */}
      <Paper elevation={2} sx={{ borderRadius: 2 }}>
        <Tabs
          value={activeTab}
          onChange={(_, v) => setActiveTab(v)}
          sx={{ borderBottom: 1, borderColor: 'divider', px: 2 }}
        >
          <Tab icon={<CloudUpload fontSize="small" />} iconPosition="start" label="Transcripts" />
          <Tab icon={<Settings fontSize="small" />} iconPosition="start" label="Customer Settings" />
        </Tabs>

        <Box sx={{ p: 2 }}>

          {/* ══ TAB 0: Transcripts ══════════════════════════════════════════ */}
          <TabPanel value={activeTab} index={0}>
            {loadingTranscripts ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
                <CircularProgress />
              </Box>
            ) : (
              <TableContainer>
                <Table>
                  <TableHead sx={{ backgroundColor: '#f5f5f5' }}>
                    <TableRow>
                      <TableCell><b>Session Name</b></TableCell>
                      <TableCell><b>File Name</b></TableCell>
                      <TableCell><b>Call Date</b></TableCell>
                      <TableCell><b>Blocks Parsed</b></TableCell>
                      <TableCell><b>Status</b></TableCell>
                      <TableCell><b>Uploaded At</b></TableCell>
                      <TableCell align="center"><b>Actions</b></TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {transcripts.length > 0 ? (
                      transcripts.map((t) => (
                        <TableRow key={t.id} hover>
                          <TableCell>{t.session_name}</TableCell>
                          <TableCell>{t.filename}</TableCell>
                          <TableCell>{new Date(t.call_date).toLocaleDateString()}</TableCell>
                          <TableCell>{t.total_blocks}</TableCell>
                          <TableCell>
                            <Chip
                              label={t.status}
                              size="small"
                              color={t.status === 'processed' ? 'success' : 'default'}
                              variant="outlined"
                            />
                          </TableCell>
                          <TableCell>{new Date(t.upload_date).toLocaleString()}</TableCell>
                          <TableCell align="center">
                            <Tooltip title="Delete this transcript and all its data">
                              <span>
                                <IconButton
                                  size="small"
                                  color="error"
                                  onClick={() => handleDeleteTranscript(t.id, t.session_name)}
                                  disabled={deletingTranscriptId === t.id}
                                >
                                  {deletingTranscriptId === t.id
                                    ? <CircularProgress size={18} color="error" />
                                    : <DeleteForever fontSize="small" />}
                                </IconButton>
                              </span>
                            </Tooltip>
                          </TableCell>
                        </TableRow>
                      ))
                    ) : (
                      <TableRow>
                        <TableCell colSpan={6} align="center" sx={{ py: 5 }}>
                          <Typography color="textSecondary">
                            No transcripts uploaded yet.
                          </Typography>
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </TabPanel>

          {/* ══ TAB 1: Customer Settings ════════════════════════════════════ */}
          <TabPanel value={activeTab} index={1}>
            {/* Deletion blocked error */}
            {deleteError && (
              <Alert
                severity="error"
                onClose={() => setDeleteError('')}
                sx={{ mb: 2, borderRadius: 2 }}
              >
                {deleteError}
              </Alert>
            )}
            {loadingCustomers ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
                <CircularProgress />
              </Box>
            ) : customers.length === 0 ? (
              <Box sx={{ textAlign: 'center', py: 6 }}>
                <Business sx={{ fontSize: 60, color: '#ccc', mb: 2 }} />
                <Typography color="textSecondary" gutterBottom>
                  No projects configured yet.
                </Typography>
                <Button
                  variant="contained"
                  startIcon={<Add />}
                  onClick={() => { setEditingCustomer(null); setCustomerFormOpen(true); }}
                >
                  Create First Project
                </Button>
              </Box>
            ) : (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {customers.map((c) => (
                  <Card
                    key={c.id}
                    elevation={activeCustomer?.id === c.id ? 4 : 1}
                    sx={{
                      borderLeft: activeCustomer?.id === c.id ? '4px solid #1976d2' : '4px solid transparent',
                      transition: 'all 0.2s'
                    }}
                  >
                    <CardContent sx={{ pb: 1 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                        <Typography variant="h6" fontWeight="bold">{c.name}</Typography>
                        {activeCustomer?.id === c.id && (
                          <Chip label="Active" size="small" color="primary" />
                        )}
                      </Box>
                      <Typography variant="body2" color="text.secondary">
                        <b>Client Speaker Name:</b> {c.client_speaker_name}
                      </Typography>
                      <Typography variant="caption" color="text.disabled">
                        Created: {c.created_at ? new Date(c.created_at).toLocaleString() : '—'}
                      </Typography>
                    </CardContent>
                    <Divider />
                    <CardActions sx={{ px: 2 }}>
                      <Button
                        size="small"
                        variant={activeCustomer?.id === c.id ? 'contained' : 'outlined'}
                        onClick={() => setActiveCustomer(c)}
                      >
                        {activeCustomer?.id === c.id ? '✓ Selected' : 'Use this Project'}
                      </Button>
                      <Tooltip title="Edit">
                        <IconButton
                          size="small"
                          onClick={() => { setEditingCustomer(c); setCustomerFormOpen(true); }}
                        >
                          <Edit fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => handleDeleteCustomer(c.id)}
                        >
                          <Delete fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </CardActions>
                  </Card>
                ))}
              </Box>
            )}
          </TabPanel>

        </Box>
      </Paper>

      {/* ── Upload Transcript Dialog */}
      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onSuccess={() => { setUploadOpen(false); fetchTranscripts(); }}
        customerId={activeCustomer?.id}
        customerName={activeCustomer?.name}
      />

      {/* ── Create / Edit Customer Dialog */}
      <CustomerFormDialog
        open={customerFormOpen}
        onClose={() => setCustomerFormOpen(false)}
        onSuccess={() => { setCustomerFormOpen(false); fetchCustomers(); }}
        existing={editingCustomer}
      />

      {/* ── Snackbar Feedback */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar(s => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setSnackbar(s => ({ ...s, open: false }))}
          severity={snackbar.severity}
          sx={{ width: '100%' }}
        >
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
        setResult(data);
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
              // Finished successfully!
              setResult({ 
                message: 'Transcript processed successfully',
                summary: statusData.summary
              });
              setUploading(false);
              setTimeout(onSuccess, 2500);
            } else if (statusData.status === 'failed') {
              // Failed in Celery
              setResult({ message: 'Processing failed in the background.' });
              setUploading(false);
            }
          } catch (e) {
            console.error("Polling error", e);
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
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        Upload Grooming Transcript
        {customerName && (
          <Typography variant="caption" display="block" color="primary">
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
            InputLabelProps={{ shrink: true }}
            required
          />
          <Button variant="outlined" component="label" fullWidth>
            {file ? file.name : 'Select DOCX Transcript'}
            <input type="file" hidden accept=".docx" onChange={(e) => setFile(e.target.files[0])} />
          </Button>

          {uploading && (
            <Box sx={{ textAlign: 'center', my: 2 }}>
              <CircularProgress size={24} sx={{ mb: 1 }} />
              <Typography variant="body2" color="textSecondary">
                Parsing transcript, extracting requirements via AI, and checking diffs... This may take a minute.
              </Typography>
            </Box>
          )}

          {result && (
            <Paper sx={{ p: 2, bgcolor: result.summary ? '#f0fff0' : '#fff0f0' }}>
              <Typography variant="subtitle2">{result.message}</Typography>
              {result.summary && (
                <Typography variant="body2" mt={1}>
                  Extracted: {result.summary.total_extracted} | Added: {result.summary.added} | Modified: {result.summary.modified}
                </Typography>
              )}
            </Paper>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={uploading}>Cancel</Button>
        <Button
          onClick={handleUpload}
          variant="contained"
          disabled={uploading || !file || !sessionName}
        >
          Upload & Process
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ─── Customer Create / Edit Dialog ───────────────────────────────────────────
function CustomerFormDialog({ open, onClose, onSuccess, existing }) {
  const [name, setName] = useState('');
  const [speakerName, setSpeakerName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Pre-fill when editing
  useEffect(() => {
    if (existing) {
      setName(existing.name);
      setSpeakerName(existing.client_speaker_name);
    } else {
      setName('');
      setSpeakerName('');
    }
    setError('');
  }, [existing, open]);

  const handleSave = async () => {
    if (!name.trim() || !speakerName.trim()) {
      setError('Both fields are required.');
      return;
    }
    setSaving(true);
    setError('');

    const formData = new FormData();
    formData.append('name', name.trim());
    formData.append('client_speaker_name', speakerName.trim());

    try {
      const url = existing
        ? `${API_URL}/requirements/customer/${existing.id}`
        : `${API_URL}/requirements/customer`;
      const method = existing ? 'PUT' : 'POST';

      const res = await fetch(url, { method, body: formData });
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || 'Save failed.');
        return;
      }
      onSuccess();
    } catch (e) {
      setError('Network error. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{existing ? 'Edit Project' : 'New Project'}</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5, mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Project Name"
            placeholder="e.g. TemPositions"
            value={name}
            onChange={(e) => setName(e.target.value)}
            fullWidth
            required
          />
          <TextField
            label="Client Speaker Name"
            placeholder="Exact name as in transcript (e.g. Prasad Kadrikar)"
            value={speakerName}
            onChange={(e) => setSpeakerName(e.target.value)}
            fullWidth
            required
            helperText="Must match the speaker name exactly as it appears in your .docx files."
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button onClick={handleSave} variant="contained" disabled={saving}>
          {saving ? <CircularProgress size={20} /> : existing ? 'Save Changes' : 'Create Project'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
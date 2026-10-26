import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, Paper, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Chip, CircularProgress, Alert, Tooltip, IconButton, Snackbar,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
  FormControl, InputLabel, Select, MenuItem,
} from '@mui/material';
import { Refresh, Visibility, Download, MenuBook, History, CallMerge, DeleteOutline } from '@mui/icons-material';
import { modulesAPI } from '../../services/api';
import { useAuth } from '../../auth/AuthContext';

/**
 * The project's requirement documents, one per product area.
 *
 * Open to every logged-in user, like Minutes: this is the written answer to "what did we agree
 * about Orders?", which is exactly what a non-admin asks the chatbot. Only editing is admin-only,
 * and that lives on the document page.
 *
 * Modules with no content are shown rather than hidden. An empty module is a real statement —
 * nothing has been agreed about it yet — and dropping those rows would make the taxonomy look
 * smaller than it is, which matters when deciding whether a meeting was filed correctly.
 */
export default function ModulesPage() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();

  const [rows, setRows] = useState([]);
  const [proposed, setProposed] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

  // Merge Dialog states
  const [mergeDialogOpen, setMergeDialogOpen] = useState(false);
  const [selectedProposal, setSelectedProposal] = useState(null);
  const [targetModuleId, setTargetModuleId] = useState('');
  const [mergeBusy, setMergeBusy] = useState(false);

  const notify = (message, severity = 'success') => setSnackbar({ open: true, message, severity });

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const load = async (showLoader = true) => {
    try {
      if (showLoader) setLoading(true);
      // Proposed modules are an admin concern — a normal user can do nothing about one.
      const [active, waiting] = await Promise.all([
        modulesAPI.list(),
        isAdmin ? modulesAPI.list({ status: 'proposed' }).catch(() => []) : Promise.resolve([]),
      ]);
      setRows(active || []);
      setProposed(waiting || []);
    } catch (err) {
      notify(err.response?.data?.detail || `Could not load documents: ${err.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const download = async (module) => {
    setBusyId(module.id);
    try {
      await modulesAPI.download(module.id, module.module_name);
    } catch (err) {
      notify(err.message || 'Download failed.', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const approve = async (module) => {
    setBusyId(module.id);
    try {
      await modulesAPI.approve(module.id);
      notify(`"${module.module_name}" approved as a new module and populated with requirements.`);
      await load(false);
    } catch (err) {
      notify(err.response?.data?.detail || err.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const openMergeDialog = (proposal) => {
    setSelectedProposal(proposal);
    setTargetModuleId('');
    setMergeDialogOpen(true);
  };

  const confirmMerge = async () => {
    if (!selectedProposal || !targetModuleId) return;
    setMergeBusy(true);
    try {
      const target = rows.find((r) => r.id === targetModuleId);
      const targetName = target ? target.module_name : 'the target module';
      await modulesAPI.merge(selectedProposal.id, targetModuleId);
      notify(`Successfully merged "${selectedProposal.module_name}" into "${targetName}".`);
      setMergeDialogOpen(false);
      setSelectedProposal(null);
      setTargetModuleId('');
      await load(false);
    } catch (err) {
      notify(err.response?.data?.detail || err.message || 'Merge failed.', 'error');
    } finally {
      setMergeBusy(false);
    }
  };

  const dismiss = async (module) => {
    if (!window.confirm(`Are you sure you want to dismiss the proposal for "${module.module_name}"?`)) {
      return;
    }
    setBusyId(module.id);
    try {
      await modulesAPI.dismiss(module.id);
      notify(`Proposal "${module.module_name}" dismissed.`);
      await load(false);
    } catch (err) {
      notify(err.response?.data?.detail || err.message || 'Dismiss failed.', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const withContent = rows.filter((r) => r.has_content).length;
  const totalRequirements = rows.reduce((sum, r) => sum + (r.requirement_count || 0), 0);

  return (
    <Box sx={{ p: 3, maxWidth: 1200, margin: '0 auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h4" fontWeight="bold">
          <MenuBook sx={{ mr: 1, verticalAlign: 'text-bottom', color: '#0d76ff' }} />
          Requirement Documents
        </Typography>
        <Button size="small" startIcon={<Refresh />} onClick={() => load()}>Refresh</Button>
      </Box>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        One living document per product area, updated after each grooming call. Open a document to
        read it, or to see what changed and when.
      </Typography>

      {/* Proposed modules triage card */}
      {proposed.length > 0 && (
        <Paper
          elevation={1}
          sx={{
            mb: 3,
            p: 2.5,
            border: '1px solid #90caf9',
            backgroundColor: '#f5f9ff',
            borderRadius: 2,
          }}
        >
          <Typography variant="subtitle1" fontWeight="bold" color="primary.main" sx={{ mb: 0.5 }}>
            {proposed.length} Proposed Module{proposed.length > 1 ? 's' : ''} Awaiting Review
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Recent transcripts discussed capabilities that were flagged as potentially new areas.
            You can <b>Approve</b> them as brand new modules, <b>Merge</b> them into an existing module, or <b>Dismiss</b> them.
          </Typography>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            {proposed.map((m) => (
              <Box
                key={m.id}
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  p: 1.5,
                  backgroundColor: '#ffffff',
                  borderRadius: 1.5,
                  border: '1px solid #e0e0e0',
                  flexWrap: 'wrap',
                  gap: 1.5,
                }}
              >
                <Box sx={{ minWidth: 260, flex: 1 }}>
                  <Typography variant="body2" fontWeight="bold">
                    {m.module_name}
                  </Typography>
                  {m.description && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.2 }}>
                      {m.description}
                    </Typography>
                  )}
                </Box>
                <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={busyId === m.id}
                    onClick={() => approve(m)}
                    sx={{ textTransform: 'none', fontSize: '0.8rem' }}
                  >
                    Approve as New
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    color="primary"
                    disabled={busyId === m.id}
                    onClick={() => openMergeDialog(m)}
                    startIcon={<CallMerge />}
                    sx={{ textTransform: 'none', fontSize: '0.8rem' }}
                  >
                    Merge into Existing...
                  </Button>
                  <Tooltip title="Dismiss / Delete this proposal">
                    <IconButton
                      size="small"
                      color="error"
                      disabled={busyId === m.id}
                      onClick={() => dismiss(m)}
                    >
                      <DeleteOutline fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Box>
            ))}
          </Box>
        </Paper>
      )}

      {/* Merge Dialog */}
      <Dialog
        open={mergeDialogOpen}
        onClose={() => !mergeBusy && setMergeDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle sx={{ fontWeight: 'bold' }}>
          Merge "{selectedProposal?.module_name}"
        </DialogTitle>
        <DialogContent dividers>
          <DialogContentText sx={{ mb: 2 }}>
            Select an existing active module to receive the requirements discussed for <b>{selectedProposal?.module_name}</b>.
            The target module document will be updated and its version incremented.
          </DialogContentText>
          <FormControl fullWidth size="small" sx={{ mt: 1 }}>
            <InputLabel id="target-module-label">Target Existing Module</InputLabel>
            <Select
              labelId="target-module-label"
              value={targetModuleId}
              label="Target Existing Module"
              onChange={(e) => setTargetModuleId(e.target.value)}
              disabled={mergeBusy}
            >
              {rows.map((row) => (
                <MenuItem key={row.id} value={row.id}>
                  {row.module_name} {row.current_version ? `(${row.current_version})` : ''}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 2 }}>
          <Button
            onClick={() => setMergeDialogOpen(false)}
            disabled={mergeBusy}
            color="inherit"
          >
            Cancel
          </Button>
          <Button
            onClick={confirmMerge}
            disabled={!targetModuleId || mergeBusy}
            variant="contained"
            color="primary"
            startIcon={mergeBusy ? <CircularProgress size={16} color="inherit" /> : <CallMerge />}
          >
            {mergeBusy ? 'Merging...' : 'Confirm & Merge'}
          </Button>
        </DialogActions>
      </Dialog>

      <Paper elevation={2} sx={{ borderRadius: 2 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}><CircularProgress /></Box>
        ) : (
          <TableContainer>
            <Table>
              <TableHead sx={{ backgroundColor: '#f5f5f5' }}>
                <TableRow>
                  <TableCell><b>Module</b></TableCell>
                  <TableCell><b>Version</b></TableCell>
                  <TableCell align="right"><b>Requirements</b></TableCell>
                  <TableCell align="right"><b>Revisions</b></TableCell>
                  <TableCell><b>Last updated</b></TableCell>
                  <TableCell align="center"><b>Actions</b></TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                      No modules yet.
                    </TableCell>
                  </TableRow>
                )}
                {rows.map((row) => {
                  const working = busyId === row.id;
                  return (
                    <TableRow
                      key={row.id}
                      hover
                      onClick={row.has_content ? () => navigate(`/documents/${row.id}`) : undefined}
                      sx={{ cursor: row.has_content ? 'pointer' : 'default' }}
                    >
                      <TableCell>
                        <Typography variant="body2" fontWeight={500}>{row.module_name}</Typography>
                        {row.description && (
                          <Typography variant="caption" color="text.secondary">
                            {row.description}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        {row.has_content
                          ? <Chip size="small" label={row.current_version} />
                          : <Chip size="small" label="No content yet" variant="outlined" />}
                      </TableCell>
                      <TableCell align="right">{row.requirement_count || '—'}</TableCell>
                      <TableCell align="right">{row.revision_count || '—'}</TableCell>
                      <TableCell>
                        {/* The meeting that last changed it, not when the pipeline ran — after a
                            backfill the latter is the same day for every module. */}
                        {row.last_call_date || row.last_synced_at
                          ? new Date(row.last_call_date || row.last_synced_at).toLocaleDateString()
                          : '—'}
                      </TableCell>
                      <TableCell align="center" onClick={(e) => e.stopPropagation()}>
                        {working ? <CircularProgress size={20} /> : row.has_content && (
                          <>
                            <Tooltip title="Open">
                              <IconButton size="small" onClick={() => navigate(`/documents/${row.id}`)}>
                                <Visibility fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="Version history">
                              <IconButton
                                size="small"
                                onClick={() => navigate(`/documents/${row.id}?tab=history`)}
                              >
                                <History fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="Download Word">
                              <IconButton size="small" onClick={() => download(row)}>
                                <Download fontSize="small" />
                              </IconButton>
                            </Tooltip>
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

      {!loading && rows.length > 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
          {withContent} of {rows.length} modules have content · {totalRequirements} requirements
          indexed for chat
        </Typography>
      )}

      <Snackbar
        open={snackbar.open}
        autoHideDuration={5000}
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

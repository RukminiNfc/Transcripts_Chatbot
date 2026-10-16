import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, Paper, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Chip, CircularProgress, Alert, Tooltip, IconButton, Snackbar,
} from '@mui/material';
import { Refresh, Visibility, Download, MenuBook, History } from '@mui/icons-material';
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
      notify(`"${module.module_name}" approved. Re-run the meetings that proposed it to fill it.`);
      await load(false);
    } catch (err) {
      notify(err.response?.data?.detail || err.message, 'error');
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

      {/* Proposed modules block the requirements of the meeting that asked for them, so this
          cannot be a quiet row at the bottom of the table. */}
      {proposed.length > 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <b>{proposed.length} module{proposed.length > 1 ? 's' : ''} awaiting approval.</b>{' '}
          A call discussed something that fits no existing area. Approving adds it to the list;
          re-run that meeting afterwards to fill it.
          <Box sx={{ mt: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
            {proposed.map((m) => (
              <Button
                key={m.id}
                size="small"
                variant="outlined"
                disabled={busyId === m.id}
                onClick={() => approve(m)}
              >
                Approve “{m.module_name}”
              </Button>
            ))}
          </Box>
        </Alert>
      )}

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

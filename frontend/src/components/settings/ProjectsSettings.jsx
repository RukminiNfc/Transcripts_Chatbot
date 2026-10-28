import React, { useState, useEffect } from 'react';
import {
  Box, Typography, Button, Paper, CircularProgress, Dialog, DialogTitle, DialogContent,
  DialogActions, TextField, Alert, IconButton, Tooltip, Divider, MenuItem, Skeleton,
} from '@mui/material';
import { Add, EditOutlined, DeleteOutline, FolderOutlined, CheckCircle } from '@mui/icons-material';
import { useProject } from '../../projects/ProjectContext';
import StatusChip from '../common/StatusChip';
import { tokens } from '../../theme/theme';
import { API_ORIGIN as API_URL } from '../../services/api';

// Moved out of the old Admin page (Admin → Customer Settings). Raw fetch is kept as it was: the
// global fetch wrapper in main.jsx attaches the JWT to every call to the backend.

const trackerSummary = (c) => (c.tracker === 'ado'
  ? `Azure Boards · ${c.ado_project} · ${c.ado_area_path}${c.ado_iteration_path ? ` · ${c.ado_iteration_path}` : ''}`
  : c.tracker === 'jira'
    ? `Jira · project ${c.jira_project_key}`
    : c.tracker === 'mantis'
      ? `Mantis · ${c.mantis_project} · ${c.mantis_category}`
      : 'No tracker — MOM tasks cannot be approved');

export default function ProjectsSettings() {
  // The active project is the app-wide selection (sidebar), so "Use this project" changes it
  // everywhere: Transcripts, Minutes, Requirements and Team & Notifications.
  const { projectId, setProjectId, reload: reloadProjects } = useProject();
  const [customers, setCustomers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [deleteError, setDeleteError] = useState(''); // the backend's blocking message

  useEffect(() => { fetchCustomers(); }, []);

  const fetchCustomers = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_URL}/requirements/customers`);
      if (res.ok) setCustomers(await res.json());
    } catch (e) {
      console.error('Failed to fetch customers:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (c) => {
    setConfirmDelete(null);
    setDeleteError('');
    try {
      const res = await fetch(`${API_URL}/requirements/customer/${c.id}`, { method: 'DELETE' });
      if (!res.ok) {
        const data = await res.json();
        // Show the backend's descriptive blocking message (e.g. it still has transcripts).
        setDeleteError(data.detail || 'Could not delete project.');
        return;
      }
      fetchCustomers();
      reloadProjects();   // sidebar list; falls back to the first project if this one was selected
    } catch {
      setDeleteError('Network error. Please try again.');
    }
  };

  const openNew = () => { setEditing(null); setFormOpen(true); };

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap', mb: 2 }}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Projects</Typography>
          <Typography variant="body2" color="text.secondary">
            Each project has its own transcripts, minutes, requirements, team and task tracker.
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<Add />} onClick={openNew}>New project</Button>
      </Box>

      {deleteError && (
        <Alert severity="error" onClose={() => setDeleteError('')} sx={{ mb: 2 }}>{deleteError}</Alert>
      )}

      {loading ? (
        [0, 1].map((i) => <Skeleton key={i} variant="rounded" height={120} sx={{ mb: 1.5 }} />)
      ) : customers.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 5, textAlign: 'center', borderRadius: 3 }}>
          <FolderOutlined sx={{ fontSize: '3rem', color: 'text.disabled', mb: 1 }} />
          <Typography color="text.secondary" gutterBottom>No projects configured yet.</Typography>
          <Button variant="contained" startIcon={<Add />} onClick={openNew}>Create first project</Button>
        </Paper>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
          {customers.map((c) => {
            const active = c.id === projectId;
            return (
              <Paper
                key={c.id}
                variant="outlined"
                sx={{
                  borderRadius: 3, overflow: 'hidden',
                  borderLeft: 4, borderLeftColor: active ? 'secondary.main' : 'transparent',
                  bgcolor: active ? tokens.sageSoft : 'background.paper',
                }}
              >
                <Box sx={{ p: 2 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
                    <Typography variant="h6">{c.name}</Typography>
                    {active && <StatusChip status="approved" label="Active" />}
                  </Box>
                  <Typography variant="body2" color="text.secondary"><b>Client speaker:</b> {c.client_speaker_name}</Typography>
                  <Typography variant="body2" color="text.secondary"><b>MOM tasks:</b> {trackerSummary(c)}</Typography>
                  <Typography variant="caption" color="text.disabled">
                    Created {c.created_at ? new Date(c.created_at).toLocaleString() : '—'}
                  </Typography>
                </Box>
                <Divider />
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 2, py: 1 }}>
                  <Button
                    size="small"
                    variant={active ? 'contained' : 'outlined'}
                    color={active ? 'secondary' : 'primary'}
                    startIcon={active ? <CheckCircle /> : null}
                    onClick={() => setProjectId(c.id)}
                    sx={{ minHeight: '2rem' }}
                  >
                    {active ? 'Selected' : 'Use this project'}
                  </Button>
                  <Box sx={{ flexGrow: 1 }} />
                  <Tooltip title="Edit">
                    <IconButton onClick={() => { setEditing(c); setFormOpen(true); }} aria-label={`Edit ${c.name}`}>
                      <EditOutlined fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Delete">
                    <IconButton color="error" onClick={() => setConfirmDelete(c)} aria-label={`Delete ${c.name}`}>
                      <DeleteOutline fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Paper>
            );
          })}
        </Box>
      )}

      {/* Delete confirmation (was window.confirm) */}
      <Dialog open={!!confirmDelete} onClose={() => setConfirmDelete(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Delete “{confirmDelete?.name}”?</DialogTitle>
        <DialogContent>
          <Typography>
            A project can only be deleted once its transcripts and requirements have been removed.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={() => handleDelete(confirmDelete)}>Delete</Button>
        </DialogActions>
      </Dialog>

      <CustomerFormDialog
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSuccess={() => { setFormOpen(false); fetchCustomers(); reloadProjects(); }}
        existing={editing}
      />
    </Box>
  );
}

// ─── Project Create / Edit Dialog ────────────────────────────────────────────
function CustomerFormDialog({ open, onClose, onSuccess, existing }) {
  const [name, setName] = useState('');
  const [speakerName, setSpeakerName] = useState('');
  // Each customer sends approved MOM tasks to ONE tracker ('' = none). The other trackers' values
  // are kept (and saved) so switching back and forth loses nothing.
  const [tracker, setTracker] = useState('');
  const [ado, setAdo] = useState({ project: '', area: '', iteration: '' });
  const [jiraKey, setJiraKey] = useState('');
  const [mantis, setMantis] = useState({ project: '', category: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Pre-fill when editing
  useEffect(() => {
    if (existing) {
      setName(existing.name);
      setSpeakerName(existing.client_speaker_name);
      setTracker(existing.tracker || '');
      setAdo({
        project: existing.ado_project || '',
        area: existing.ado_area_path || '',
        iteration: existing.ado_iteration_path || '',
      });
      setJiraKey(existing.jira_project_key || '');
      setMantis({ project: existing.mantis_project || '', category: existing.mantis_category || '' });
    } else {
      setName('');
      setSpeakerName('');
      setTracker('');
      setAdo({ project: '', area: '', iteration: '' });
      setJiraKey('');
      setMantis({ project: '', category: '' });
    }
    setError('');
  }, [existing, open]);

  const handleSave = async () => {
    if (!name.trim() || !speakerName.trim()) {
      setError('Project name and client speaker name are required.');
      return;
    }
    // Mirror the backend's rules so the admin sees the problem before the round trip.
    if (tracker === 'ado' && !(ado.project.trim() && ado.area.trim())) {
      setError('Azure Boards needs both an Azure DevOps project and an area path.');
      return;
    }
    if (tracker === 'jira' && !/^[A-Za-z][A-Za-z0-9_]+$/.test(jiraKey.trim())) {
      setError('Enter the Jira project key — letters/digits, e.g. CAL (not the project name).');
      return;
    }
    if (tracker === 'mantis' && !(mantis.project.trim() && mantis.category.trim())) {
      setError('Mantis needs both a project name and a category.');
      return;
    }
    setSaving(true);
    setError('');

    const formData = new FormData();
    formData.append('name', name.trim());
    formData.append('client_speaker_name', speakerName.trim());
    // Always sent: an empty value clears the setting on the backend.
    formData.append('tracker', tracker);
    formData.append('ado_project', ado.project.trim());
    formData.append('ado_area_path', ado.area.trim());
    formData.append('ado_iteration_path', ado.iteration.trim());
    formData.append('jira_project_key', jiraKey.trim().toUpperCase());
    formData.append('mantis_project', mantis.project.trim());
    formData.append('mantis_category', mantis.category.trim());

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
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{existing ? 'Edit project' : 'New project'}</DialogTitle>
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

          <Divider textAlign="left"><Typography variant="caption" color="text.secondary">MOM tasks</Typography></Divider>
          <TextField
            select
            label="Task tracker"
            value={tracker}
            onChange={(e) => { setTracker(e.target.value); setError(''); }}
            fullWidth
            helperText="Where approved MOM action items are created as Tasks."
          >
            <MenuItem value=""><em>None — approving is disabled</em></MenuItem>
            <MenuItem value="ado">Azure Boards</MenuItem>
            <MenuItem value="jira">Jira</MenuItem>
            <MenuItem value="mantis">Mantis</MenuItem>
          </TextField>

          {tracker === 'ado' && (
            <>
              <TextField
                label="Azure DevOps Project"
                placeholder="e.g. IntelliStaff"
                value={ado.project}
                onChange={(e) => setAdo((a) => ({ ...a, project: e.target.value }))}
                fullWidth
                required
              />
              <TextField
                label="Area Path"
                placeholder="e.g. IntelliStaff\Client-X"
                value={ado.area}
                onChange={(e) => setAdo((a) => ({ ...a, area: e.target.value }))}
                fullWidth
                required
              />
              <TextField
                label="Iteration Path (optional)"
                placeholder="e.g. IntelliStaff\Sprint 42"
                value={ado.iteration}
                onChange={(e) => setAdo((a) => ({ ...a, iteration: e.target.value }))}
                fullWidth
                helperText="Leave empty to use the project's default iteration."
              />
            </>
          )}

          {tracker === 'jira' && (
            <TextField
              label="Jira Project Key"
              placeholder="e.g. CAL"
              value={jiraKey}
              onChange={(e) => setJiraKey(e.target.value.toUpperCase())}
              fullWidth
              required
              helperText="The short key shown in issue IDs (CAL-123), not the project name. The Jira site itself is set in the backend .env."
            />
          )}

          {tracker === 'mantis' && (
            <>
              <TextField
                label="Mantis Project"
                placeholder="e.g. Transcript Chatbot"
                value={mantis.project}
                onChange={(e) => setMantis((m) => ({ ...m, project: e.target.value }))}
                name="mantis_project"
                autoComplete="off"   // otherwise the browser suggests unrelated earlier entries
                fullWidth
                required
                helperText="Exact project name as shown in Mantis. Checked against Mantis when minutes are approved."
              />
              <TextField
                label="Category"
                placeholder="e.g. General"
                value={mantis.category}
                onChange={(e) => setMantis((m) => ({ ...m, category: e.target.value }))}
                name="mantis_category"
                autoComplete="off"
                fullWidth
                required
                helperText="Must exist in that project. Assignees are matched by email prefix (jdoe@… → jdoe)."
              />
            </>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button onClick={handleSave} variant="contained" disabled={saving}>
          {saving ? <CircularProgress size={20} /> : existing ? 'Save changes' : 'Create project'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

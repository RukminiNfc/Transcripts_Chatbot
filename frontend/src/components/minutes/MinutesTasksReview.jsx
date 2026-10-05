import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box, Paper, Typography, Button, Alert, CircularProgress, Chip, IconButton, Tooltip, Snackbar,
  Dialog, DialogTitle, DialogContent, DialogActions, Drawer, TextField, MenuItem, Divider, Link,
} from '@mui/material';
import {
  ArrowBack, Edit, Delete, TaskAlt, Replay, OpenInNew, PersonOff, Event,
} from '@mui/icons-material';
import MarkdownRenderer from '../chat/MarkdownRenderer';
import { momAPI } from '../../services/api';

/**
 * Review a MOM's action items before they become Azure Boards Tasks.
 *
 * The backend extracts the "Consolidated Action Items" into draft rows on first open; every edit
 * and delete here is saved immediately, so leaving the page loses nothing. Approve creates one
 * Task per remaining row. After that the list is read-only apart from fixing and retrying
 * failures — a created Task is edited in Azure Boards, not here.
 *
 * The MOM's own Action Items section sits alongside the list so each task can be checked against
 * what the minutes actually say.
 */

const STATUS_CHIP = {
  draft: null,
  pushing: <Chip size="small" label="Sending…" color="info" />,
  created: <Chip size="small" label="Created" color="success" />,
  failed: <Chip size="small" label="Failed" color="error" />,
};

// Just the section the tasks came from, so the side panel matches the list.
const actionItemsSection = (markdown) => {
  const m = /^##\s+Consolidated Action Items\s*$[\s\S]*?(?=^##\s|(?![\s\S]))/im.exec(markdown || '');
  return m ? m[0] : '';
};

const formatDate = (iso) =>
  iso ? new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '';

export default function MinutesTasksReview() {
  const { momId } = useParams();
  const navigate = useNavigate();

  const [mom, setMom] = useState(null);
  const [review, setReview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);          // item being edited in the drawer
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [confirmApprove, setConfirmApprove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

  const notify = (message, severity = 'success') => setSnackbar({ open: true, message, severity });
  const errText = (err) => err.response?.data?.detail || err.message;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const [m, r] = await Promise.all([momAPI.get(momId), momAPI.reviewTasks(momId)]);
        if (!cancelled) { setMom(m); setReview(r); }
      } catch (err) {
        if (!cancelled) setError(errText(err) || 'Could not load the tasks.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [momId]);

  const items = review?.items || [];
  const approved = !!review?.approved_at;
  const drafts = items.filter((i) => i.push_status === 'draft');
  const failed = items.filter((i) => i.push_status === 'failed');
  const unassignedDrafts = drafts.filter((i) => !i.assignee_email).length;

  const nameByEmail = useMemo(() => {
    const map = {};
    (review?.assignees || []).forEach((a) => { map[a.email.toLowerCase()] = a.name || a.email; });
    return map;
  }, [review]);

  const assigneeLabel = (email) => (email ? nameByEmail[email.toLowerCase()] || email : '');
  const replaceItem = (updated) =>
    setReview((r) => ({ ...r, items: r.items.map((i) => (i.id === updated.id ? updated : i)) }));

  const saveEdit = async (changes) => {
    setBusy(true);
    try {
      replaceItem(await momAPI.updateTask(momId, editing.id, changes));
      setEditing(null);
      notify('Task saved.');
    } catch (err) {
      notify(errText(err), 'error');
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    const item = confirmDelete;
    setConfirmDelete(null);
    setBusy(true);
    try {
      await momAPI.deleteTask(momId, item.id);
      setReview((r) => ({ ...r, items: r.items.filter((i) => i.id !== item.id) }));
      notify('Task removed — it will not be sent to Azure Boards.');
    } catch (err) {
      notify(errText(err), 'error');
    } finally {
      setBusy(false);
    }
  };

  const applyPushResult = (result) => {
    setReview((r) => ({ ...r, approved_at: result.approved_at, approved_by: result.approved_by, items: result.items }));
    if (result.failed) notify(`${result.created} task(s) created, ${result.failed} failed — fix and retry below.`, 'warning');
    else notify(`${result.created} task(s) created in Azure Boards.`);
  };

  const doApprove = async () => {
    setConfirmApprove(false);
    setBusy(true);
    try {
      applyPushResult(await momAPI.approve(momId));
    } catch (err) {
      notify(errText(err), 'error');
    } finally {
      setBusy(false);
    }
  };

  const doRetry = async () => {
    setBusy(true);
    try {
      applyPushResult(await momAPI.retryTasks(momId));
    } catch (err) {
      notify(errText(err), 'error');
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', p: 8 }}><CircularProgress /></Box>;
  }

  const callDate = mom?.call_date ? new Date(mom.call_date).toLocaleDateString(undefined, {
    day: 'numeric', month: 'long', year: 'numeric',
  }) : '';

  return (
    <Box sx={{ p: 3, maxWidth: 1400, margin: '0 auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2, gap: 2, flexWrap: 'wrap' }}>
        <Button startIcon={<ArrowBack />} onClick={() => navigate('/minutes')}>Back to Minutes</Button>
        {review && !approved && (
          <Button
            variant="contained"
            startIcon={busy ? <CircularProgress size={18} color="inherit" /> : <TaskAlt />}
            disabled={busy || !review.tracker_configured || drafts.length === 0}
            onClick={() => setConfirmApprove(true)}
          >
            Approve & create {drafts.length} task{drafts.length === 1 ? '' : 's'}
          </Button>
        )}
        {approved && failed.length > 0 && (
          <Button
            variant="contained" color="warning"
            startIcon={busy ? <CircularProgress size={18} color="inherit" /> : <Replay />}
            disabled={busy}
            onClick={doRetry}
          >
            Retry {failed.length} failed
          </Button>
        )}
      </Box>

      <Typography variant="h5" fontWeight="bold">Review tasks</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {mom?.session_name}{callDate ? ` · ${callDate}` : ''}
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {review && !review.tracker_configured && !approved && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          This project has no Azure DevOps project / area path set (or the integration is disabled).
          You can review and edit tasks now; add the ADO settings in Admin → Customer Settings before approving.
        </Alert>
      )}
      {approved && (
        <Alert severity="success" sx={{ mb: 2 }}>
          Approved by <b>{review.approved_by}</b> on {new Date(review.approved_at).toLocaleString()}.
          Created tasks are edited in Azure Boards; failed ones can be fixed here and retried.
        </Alert>
      )}

      {review && (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '3fr 2fr' }, gap: 3, alignItems: 'start' }}>
          {/* ── Task list */}
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <Typography variant="subtitle2" color="text.secondary">
              {approved
                ? `${items.filter((i) => i.push_status === 'created').length} of ${items.length} task(s) created`
                : `${drafts.length} task(s) will be created`}
              {!approved && unassignedDrafts > 0 && ` · ${unassignedDrafts} unassigned`}
            </Typography>

            {items.length === 0 && (
              <Paper sx={{ p: 4, textAlign: 'center', color: 'text.secondary' }}>
                No action items {mom?.content_markdown ? 'left to create.' : 'found in these minutes.'}
              </Paper>
            )}

            {items.map((item) => {
              const editable = item.push_status === 'draft' || item.push_status === 'failed';
              return (
                <Paper key={item.id} variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
                  <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      {item.area && (
                        <Typography variant="caption" color="primary" fontWeight="bold">{item.area}</Typography>
                      )}
                      <Typography variant="body1" fontWeight={500}>{item.title}</Typography>
                      {item.description && item.description !== item.title && (
                        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>
                          {item.description}
                        </Typography>
                      )}

                      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 1, alignItems: 'center' }}>
                        {item.assignee_email ? (
                          <Chip size="small" label={assigneeLabel(item.assignee_email)} />
                        ) : (
                          <Chip size="small" icon={<PersonOff />} label="Unassigned" variant="outlined" color="warning" />
                        )}
                        {item.due_date ? (
                          <Chip size="small" icon={<Event />} label={formatDate(item.due_date)} variant="outlined" />
                        ) : item.due_text ? (
                          <Tooltip title="Could not turn this into a date — edit to set one.">
                            <Chip size="small" icon={<Event />} label={`“${item.due_text}”`} variant="outlined" color="warning" />
                          </Tooltip>
                        ) : null}
                        {item.owner_name && (
                          <Typography variant="caption" color="text.secondary">MOM owner: {item.owner_name}</Typography>
                        )}
                        {STATUS_CHIP[item.push_status]}
                        {item.external_url && (
                          <Link href={item.external_url} target="_blank" rel="noopener" variant="caption"
                                sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.3 }}>
                            {/* ADO ids are bare numbers ("#1226"); Jira keys carry their own prefix. */}
                            {item.tracker === 'ado' ? `#${item.external_key}` : item.external_key}
                            <OpenInNew sx={{ fontSize: 14 }} />
                          </Link>
                        )}
                      </Box>
                      {item.push_error && (
                        <Alert severity="error" sx={{ mt: 1, py: 0 }}>{item.push_error}</Alert>
                      )}
                    </Box>

                    {editable && (
                      <Box sx={{ display: 'flex' }}>
                        <Tooltip title="Edit">
                          <span>
                            <IconButton size="small" disabled={busy} onClick={() => setEditing(item)}>
                              <Edit fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                        <Tooltip title="Delete">
                          <span>
                            <IconButton size="small" color="error" disabled={busy} onClick={() => setConfirmDelete(item)}>
                              <Delete fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                      </Box>
                    )}
                  </Box>
                </Paper>
              );
            })}
          </Box>

          {/* ── Source: the MOM's own Action Items section */}
          <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, position: { lg: 'sticky' }, top: { lg: 16 },
                                         maxHeight: { lg: 'calc(100vh - 32px)' }, overflowY: 'auto' }}>
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>From the minutes</Typography>
            <Divider sx={{ mb: 1 }} />
            {actionItemsSection(mom?.content_markdown)
              ? <MarkdownRenderer content={actionItemsSection(mom.content_markdown)} />
              : <Typography variant="body2" color="text.secondary">No “Consolidated Action Items” section.</Typography>}
          </Paper>
        </Box>
      )}

      <EditTaskDrawer
        item={editing}
        assignees={review?.assignees || []}
        saving={busy}
        onClose={() => setEditing(null)}
        onSave={saveEdit}
      />

      {/* ── Delete confirmation */}
      <Dialog open={!!confirmDelete} onClose={() => setConfirmDelete(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Delete this task?</DialogTitle>
        <DialogContent>
          <Typography>{confirmDelete?.title}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            It will not be created in Azure Boards. The minutes themselves are unchanged.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={doDelete}>Delete</Button>
        </DialogActions>
      </Dialog>

      {/* ── Approve confirmation. Deliberate friction: this writes to Azure Boards. */}
      <Dialog open={confirmApprove} onClose={() => setConfirmApprove(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Approve these minutes?</DialogTitle>
        <DialogContent>
          <Typography gutterBottom>
            This creates <b>{drafts.length}</b> Task{drafts.length === 1 ? '' : 's'} in Azure Boards. It does not email anyone.
          </Typography>
          {unassignedDrafts > 0 && (
            <Alert severity="warning" sx={{ mt: 1 }}>
              {unassignedDrafts} task(s) have no assignee and will be created unassigned.
            </Alert>
          )}
          <Alert severity="info" sx={{ mt: 1 }}>
            After approving, tasks can no longer be edited or deleted here.
          </Alert>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmApprove(false)}>Cancel</Button>
          <Button variant="contained" onClick={doApprove}>Approve & create</Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={5000}
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

// ─── Edit drawer ─────────────────────────────────────────────────────────────
function EditTaskDrawer({ item, saving, onClose, ...rest }) {
  return (
    <Drawer anchor="right" open={!!item} onClose={saving ? undefined : onClose}>
      {/* Keyed by item so each open starts from that item's saved values. */}
      {item && <EditTaskForm key={item.id} item={item} saving={saving} onClose={onClose} {...rest} />}
    </Drawer>
  );
}

function EditTaskForm({ item, assignees, saving, onClose, onSave }) {
  const [form, setForm] = useState(() => ({
    title: item.title || '',
    description: item.description || '',
    area: item.area || '',
    assignee_email: item.assignee_email || '',
    due_date: item.due_date || '',
  }));

  const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));
  const titleError = !form.title?.trim() ? 'Title is required.' : form.title.length > 255 ? 'Max 255 characters.' : '';

  // The current assignee may not be an active subscriber any more — keep it selectable.
  const options = [...assignees];
  if (form.assignee_email && !options.some((a) => a.email.toLowerCase() === form.assignee_email.toLowerCase())) {
    options.push({ name: null, email: form.assignee_email });
  }

  const save = () => onSave({
    title: form.title.trim(),
    description: form.description,
    area: form.area.trim() || null,
    assignee_email: form.assignee_email || null,
    due_date: form.due_date || null,
  });

  return (
      <Box sx={{ width: { xs: '100vw', sm: 480 }, p: 3, display: 'flex', flexDirection: 'column', gap: 2.5 }}>
        <Typography variant="h6" fontWeight="bold">Edit task</Typography>

        <TextField label="Title" value={form.title || ''} onChange={set('title')} required fullWidth
                   error={!!titleError} helperText={titleError || `${form.title?.length || 0}/255`} />
        <TextField label="Description" value={form.description || ''} onChange={set('description')}
                   multiline minRows={4} fullWidth />
        <TextField label="Area" value={form.area || ''} onChange={set('area')} fullWidth />

        <TextField select label="Assigned to" value={form.assignee_email || ''} onChange={set('assignee_email')} fullWidth
                   helperText={item?.owner_name ? `Owner in the minutes: ${item.owner_name}` : undefined}>
          <MenuItem value=""><em>Unassigned</em></MenuItem>
          {options.map((a) => (
            <MenuItem key={a.email} value={a.email}>
              {a.name ? `${a.name} — ${a.email}` : a.email}
            </MenuItem>
          ))}
        </TextField>

        <TextField type="date" label="Due date" value={form.due_date || ''} onChange={set('due_date')} fullWidth
                   slotProps={{ inputLabel: { shrink: true } }}
                   helperText={item?.due_text ? `In the minutes: “${item.due_text}”` : undefined} />

        <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, mt: 1 }}>
          <Button onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="contained" onClick={save} disabled={saving || !!titleError}>
            {saving ? <CircularProgress size={20} color="inherit" /> : 'Save'}
          </Button>
        </Box>
      </Box>
  );
}

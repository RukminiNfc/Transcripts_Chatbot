import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, FormControlLabel, IconButton, Paper, Skeleton, Snackbar, Switch, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, TextField, Tooltip, Typography, useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Add, DeleteOutline, EditOutlined, LibraryAddOutlined } from '@mui/icons-material';
import { subscriptionsAPI } from '../../services/api';
import { useProject } from '../../projects/ProjectContext';

// Same loose check the backend applies (subscriptions.py _EMAIL_RE), so most typos are caught
// before the round trip; the server stays the authority.
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const errorText = (err) => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join('; ');
  return err?.message || 'Something went wrong.';
};

const emailKey = (addr) => (addr || '').trim().toLowerCase();
const listNames = (names) => (names.length <= 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`);

/**
 * Multi-select of projects. `fixed` ids are memberships that already exist: shown checked but
 * locked, because removing someone from a project is done with Remove on that project's team.
 */
function ProjectsPicker({ projects, value, fixed = [], onChange, error, helperText }) {
  const fixedSet = new Set(fixed);
  const nameOf = (id) => projects.find((p) => p.id === id)?.name || 'Unknown project';
  return (
    <Autocomplete
      multiple
      disableCloseOnSelect
      disableClearable={fixed.length > 0}
      options={projects.map((p) => p.id)}
      value={value}
      getOptionLabel={nameOf}
      getOptionDisabled={(id) => fixedSet.has(id)}
      // Keep locked memberships even if the user backspaces over their chips.
      onChange={(_, next) => onChange([...fixed.filter((id) => !next.includes(id)), ...next])}
      renderOption={(props, id, { selected }) => {
        const { key, ...rest } = props;
        return (
          <li key={key} {...rest}>
            <Checkbox size="small" checked={selected} sx={{ mr: 1, p: 0.5 }} />
            <Box sx={{ flexGrow: 1 }}>{nameOf(id)}</Box>
            {fixedSet.has(id) && <Typography variant="caption" color="text.secondary">Member</Typography>}
          </li>
        );
      }}
      renderValue={(ids, getItemProps) => ids.map((id, index) => {
        const { key, onDelete, ...itemProps } = getItemProps({ index });
        return (
          <Chip key={key} size="small" label={nameOf(id)} {...itemProps}
                onDelete={fixedSet.has(id) ? undefined : onDelete} />
        );
      })}
      renderInput={(params) => (
        <TextField {...params} label="Projects" placeholder={value.length ? '' : 'Pick projects'}
                   error={error} helperText={helperText} />
      )}
    />
  );
}

/**
 * The selected project's team (team_subscriptions). These rows drive the Minutes email, the
 * requirement-change notifications, and the assignee dropdown in task review — all of which
 * use ACTIVE members only. Pausing keeps someone's details without emailing them.
 *
 * There is no "person" table: one row per project, and rows sharing an email (case-insensitive)
 * are the same person. That is what "Also in", "Add to projects" and "all projects" act on.
 */
export default function TeamSubscriptions({ projectId, projectName }) {
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));
  const { projects } = useProject();

  const [all, setAll] = useState([]);               // every project's rows
  const [loading, setLoading] = useState(false);
  const [togglingId, setTogglingId] = useState(null);
  // { mode: 'add' | 'edit' | 'projects', id?, member_name, email_address, orig?, projectIds, fixedIds, applyAll }
  const [form, setForm] = useState(null);
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [removeEverywhere, setRemoveEverywhere] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

  const notify = (message, severity = 'success') => setSnackbar({ open: true, message, severity });

  const load = useCallback(async () => {
    if (!projectId) { setAll([]); return; }
    setLoading(true);
    try {
      setAll(await subscriptionsAPI.list());
    } catch (err) {
      notify(`Could not load the team: ${errorText(err)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const members = useMemo(() => all.filter((r) => r.customer_id === projectId), [all, projectId]);
  const rowsByEmail = useMemo(() => {
    const map = new Map();
    all.forEach((r) => {
      const k = emailKey(r.email_address);
      map.set(k, [...(map.get(k) || []), r]);
    });
    return map;
  }, [all]);

  const nameOfProject = (id) => projects.find((p) => p.id === id)?.name || 'Unknown project';
  const memberProjectIds = (m) => (rowsByEmail.get(emailKey(m.email_address)) || []).map((r) => r.customer_id);
  const otherProjectNames = (m) => memberProjectIds(m).filter((id) => id !== projectId).map(nameOfProject);
  const who = (m) => m.member_name || m.email_address;

  const activeCount = members.filter((m) => m.is_active).length;

  // ── add / edit / add to projects ──
  const openAdd = () => {
    setFormError('');
    setForm({ mode: 'add', member_name: '', email_address: '', projectIds: [projectId], fixedIds: [] });
  };
  const openEdit = (m, mode = 'edit') => {
    const ids = memberProjectIds(m);
    setFormError('');
    setForm({
      mode, id: m.id, member_name: m.member_name || '', email_address: m.email_address,
      orig: { member_name: m.member_name || '', email_address: m.email_address },
      projectIds: ids, fixedIds: ids, applyAll: true,
    });
  };

  const bulkMessage = (person, { created, skipped }) => {
    const parts = [];
    if (created.length) parts.push(`${person} added to ${listNames(created.map((r) => nameOfProject(r.customer_id)))}.`);
    if (skipped.length) parts.push(`Already in ${listNames(skipped.map(nameOfProject))}.`);
    return parts.join(' ');
  };

  const save = async () => {
    const name = form.member_name.trim();
    const email = form.email_address.trim();
    if (!EMAIL_RE.test(email)) { setFormError('Enter a valid email address.'); return; }
    if (form.projectIds.length === 0) { setFormError('Pick at least one project.'); return; }
    const person = name || email;
    setSaving(true);
    setFormError('');
    try {
      if (form.mode === 'add') {
        const result = await subscriptionsAPI.addToProjects({ customer_ids: form.projectIds, member_name: name, email_address: email });
        notify(bulkMessage(person, result), result.created.length ? 'success' : 'info');
      } else {
        const messages = [];
        const changed = name !== form.orig.member_name || email !== form.orig.email_address;
        if (changed) {
          const hasOthers = form.fixedIds.length > 1;
          await subscriptionsAPI.update(form.id, {
            member_name: name, email_address: email, apply_to_all_projects: hasOthers && form.applyAll,
          });
          messages.push('Member updated.');
        }
        const added = form.projectIds.filter((id) => !form.fixedIds.includes(id));
        if (added.length) {
          const result = await subscriptionsAPI.addToProjects({ customer_ids: added, member_name: name, email_address: email });
          messages.push(bulkMessage(person, result));
        }
        notify(messages.join(' ') || 'No changes.', messages.length ? 'success' : 'info');
      }
      setForm(null);
      await load();
    } catch (err) {
      setFormError(errorText(err)); // e.g. "Email already subscribed for this project"
    } finally {
      setSaving(false);
    }
  };

  // ── pause / resume (this project only): optimistic, rolled back on failure ──
  const toggleActive = async (m) => {
    const next = !m.is_active;
    const setActive = (value) => setAll((list) => list.map((x) => (x.id === m.id ? { ...x, is_active: value } : x)));
    setTogglingId(m.id);
    setActive(next);
    try {
      await subscriptionsAPI.update(m.id, { is_active: next });
    } catch (err) {
      setActive(!next);
      notify(`Could not ${next ? 'resume' : 'pause'} ${who(m)}: ${errorText(err)}`, 'error');
    } finally {
      setTogglingId(null);
    }
  };

  const askRemove = (m) => { setRemoveEverywhere(false); setConfirmDelete(m); };

  const remove = async (m) => {
    const everywhere = removeEverywhere && otherProjectNames(m).length > 0;
    setConfirmDelete(null);
    try {
      await subscriptionsAPI.remove(m.id, { allProjects: everywhere });
      notify(`${who(m)} removed${everywhere ? ' from all projects' : ''}.`);
      await load();
    } catch (err) {
      notify(`Could not remove: ${errorText(err)}`, 'error');
    }
  };

  if (!projectId) {
    return <Alert severity="info">Create a project under Projects first.</Alert>;
  }

  const activeSwitch = (m) => (
    <Tooltip title={m.is_active ? 'Receiving emails for this project — click to pause' : 'Paused — click to resume'}>
      <Switch
        checked={!!m.is_active}
        disabled={togglingId === m.id}
        onChange={() => toggleActive(m)}
        color="secondary"
        inputProps={{ 'aria-label': `${m.is_active ? 'Pause' : 'Resume'} ${who(m)}` }}
      />
    </Tooltip>
  );

  const alsoIn = (m) => {
    const names = otherProjectNames(m);
    if (!names.length) return <Typography variant="body2" color="text.disabled">—</Typography>;
    const shown = names.slice(0, 2);
    return (
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
        {shown.map((n) => <Chip key={n} size="small" variant="outlined" label={n} />)}
        {names.length > shown.length && (
          <Tooltip title={names.slice(2).join(', ')}>
            <Chip size="small" variant="outlined" label={`+${names.length - shown.length}`} />
          </Tooltip>
        )}
      </Box>
    );
  };

  const rowActions = (m) => {
    const inEvery = memberProjectIds(m).length >= projects.length;
    return (
      <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Tooltip title={inEvery ? 'Already in every project' : 'Add to projects'}>
          <span>
            <IconButton onClick={() => openEdit(m, 'projects')} disabled={inEvery} aria-label={`Add ${who(m)} to projects`}>
              <LibraryAddOutlined fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Edit">
          <IconButton onClick={() => openEdit(m)} aria-label={`Edit ${who(m)}`}><EditOutlined fontSize="small" /></IconButton>
        </Tooltip>
        <Tooltip title="Remove">
          <IconButton onClick={() => askRemove(m)} aria-label={`Remove ${who(m)}`}><DeleteOutline fontSize="small" /></IconButton>
        </Tooltip>
      </Box>
    );
  };

  const otherCount = form && form.mode !== 'add' ? form.fixedIds.length - 1 : 0;
  const deleteOthers = confirmDelete ? otherProjectNames(confirmDelete) : [];
  const dialogTitle = form?.mode === 'add' ? 'Add member'
    : form?.mode === 'projects' ? `Add ${form.orig.member_name || form.orig.email_address} to projects`
      : 'Edit member';

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap', mb: 2 }}>
        <Box sx={{ maxWidth: '44rem' }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            {projectName ? `${projectName} team` : 'Project team'}
            {members.length > 0 && (
              <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                {activeCount} active · {members.length - activeCount} paused
              </Typography>
            )}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            These people receive Minutes emails and requirement-change notifications, and appear as
            assignees when reviewing tasks. Paused members keep their details but get no email.
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<Add />} onClick={openAdd}>Add member</Button>
      </Box>

      {loading ? (
        [0, 1, 2].map((i) => <Skeleton key={i} variant="rounded" height={52} sx={{ mb: 1 }} />)
      ) : members.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 4, textAlign: 'center', color: 'text.secondary', borderRadius: 3 }}>
          No one will receive emails for {projectName || 'this project'} yet — add the first member.
        </Paper>
      ) : isDesktop ? (
        <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 3 }}>
          <Table size="small" sx={{ '& td': { py: 1 } }}>
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Email</TableCell>
                <TableCell>Also in</TableCell>
                <TableCell sx={{ width: '6rem' }}>Active</TableCell>
                <TableCell sx={{ width: '9rem' }} aria-label="Actions" />
              </TableRow>
            </TableHead>
            <TableBody>
              {members.map((m) => (
                <TableRow key={m.id} hover sx={{ opacity: m.is_active ? 1 : 0.6 }}>
                  <TableCell sx={{ fontWeight: 500 }}>{m.member_name || '—'}</TableCell>
                  <TableCell>{m.email_address}</TableCell>
                  <TableCell>{alsoIn(m)}</TableCell>
                  <TableCell>{activeSwitch(m)}</TableCell>
                  <TableCell>{rowActions(m)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      ) : (
        members.map((m) => {
          const others = otherProjectNames(m);
          return (
            <Paper key={m.id} variant="outlined" sx={{ p: 1.5, mb: 1, borderRadius: 3, opacity: m.is_active ? 1 : 0.6 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                  <Typography sx={{ fontWeight: 500 }} noWrap>{who(m)}</Typography>
                  {m.member_name && <Typography variant="body2" color="text.secondary" noWrap>{m.email_address}</Typography>}
                </Box>
                {activeSwitch(m)}
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}>
                <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1, minWidth: 0 }} noWrap>
                  {others.length ? `Also in: ${others.join(', ')}` : 'Only in this project'}
                </Typography>
                {rowActions(m)}
              </Box>
            </Paper>
          );
        })
      )}

      {/* Add / edit / add to projects */}
      <Dialog open={!!form} onClose={saving ? undefined : () => setForm(null)} maxWidth="xs" fullWidth>
        <DialogTitle>{dialogTitle}</DialogTitle>
        <DialogContent>
          <Box
            component="form"
            id="member-form"
            onSubmit={(e) => { e.preventDefault(); save(); }}
            sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}
          >
            {form?.mode !== 'projects' && (
              <>
                <TextField
                  label="Name"
                  value={form?.member_name || ''}
                  onChange={(e) => setForm((f) => ({ ...f, member_name: e.target.value }))}
                  autoFocus
                  fullWidth
                />
                <TextField
                  label="Email"
                  type="email"
                  required
                  value={form?.email_address || ''}
                  onChange={(e) => { setFormError(''); setForm((f) => ({ ...f, email_address: e.target.value })); }}
                  error={!!formError && form?.mode !== 'projects'}
                  helperText={(form?.mode !== 'projects' && formError) || ' '}
                  fullWidth
                />
              </>
            )}
            {form && (
              <ProjectsPicker
                projects={projects}
                value={form.projectIds}
                fixed={form.fixedIds}
                onChange={(ids) => { setFormError(''); setForm((f) => ({ ...f, projectIds: ids })); }}
                error={form.mode === 'projects' && !!formError}
                helperText={(form.mode === 'projects' && formError)
                  || (form.mode === 'add' ? 'They are added to each project picked.' : 'Pick projects to add them to.')}
              />
            )}
            {form?.mode === 'edit' && otherCount > 0 && (
              <FormControlLabel
                control={<Checkbox checked={!!form.applyAll} onChange={(e) => setForm((f) => ({ ...f, applyAll: e.target.checked }))} />}
                label={`Also update name and email in ${otherCount} other project${otherCount > 1 ? 's' : ''}`}
              />
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setForm(null)} disabled={saving}>Cancel</Button>
          <Button type="submit" form="member-form" variant="contained"
                  disabled={saving || !form?.email_address?.trim()
                    || (form?.mode === 'projects' && form.projectIds.length === form.fixedIds.length)}
                  startIcon={saving ? <CircularProgress size={16} color="inherit" /> : null}>
            {form?.mode === 'edit' ? 'Save' : 'Add'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Remove confirmation */}
      <Dialog open={!!confirmDelete} onClose={() => setConfirmDelete(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Remove member?</DialogTitle>
        <DialogContent>
          <Typography>
            <b>{confirmDelete && who(confirmDelete)}</b> will stop receiving emails for{' '}
            {removeEverywhere && deleteOthers.length ? 'any project' : (projectName || 'this project')} and won't appear
            as a task assignee. To keep their details, pause them instead.
          </Typography>
          {deleteOthers.length > 0 && (
            <FormControlLabel
              sx={{ mt: 1.5 }}
              control={<Checkbox checked={removeEverywhere} onChange={(e) => setRemoveEverywhere(e.target.checked)} />}
              label={`Also remove from ${listNames(deleteOthers)}`}
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={() => remove(confirmDelete)}>
            {removeEverywhere && deleteOthers.length ? 'Remove everywhere' : 'Remove'}
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4500}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity={snackbar.severity} onClose={() => setSnackbar((s) => ({ ...s, open: false }))} sx={{ width: '100%' }}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}

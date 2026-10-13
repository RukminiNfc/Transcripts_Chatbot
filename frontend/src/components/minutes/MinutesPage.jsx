import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, Paper, Chip, CircularProgress, Dialog, DialogTitle, DialogContent,
  DialogActions, Alert, Tooltip, IconButton, Snackbar, Menu, MenuItem, ListItemIcon, Select,
  Skeleton, Drawer, Radio, RadioGroup, FormControlLabel, useMediaQuery,
} from '@mui/material';
import { useTheme, alpha } from '@mui/material/styles';
import {
  Send, Autorenew, Download, DescriptionOutlined, Description, PlaylistAddCheck, MoreVert, Add,
  ArrowForward, OpenInNew, Refresh,
} from '@mui/icons-material';
import { momAPI } from '../../services/api';
import { useAuth } from '../../auth/AuthContext';
import { useProject } from '../../projects/ProjectContext';
import PageHeader from '../../layout/PageHeader';
import StatusChip from '../common/StatusChip';
import MinutesDetailPanel from './MinutesDetailPanel';
import { STATUS, statusOf, failureReason } from './minutesStatus';
import { formatCallDate } from './momParse';
import { tokens } from '../../theme/theme';

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
 * Status (pills, chips) is admin-only too: for a reader every row already has minutes, so a
 * Draft/Sent label is noise against a document they can simply open.
 *
 * No version column: regenerating produces a new row for audit, but the UI always shows the
 * latest, and a version number here would be confused with REQUIREMENT versions, which are a
 * different concept entirely.
 */

// Filter pills, in display order. Not generated / Failed appear only when they have entries.
const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'draft', label: 'Drafts' },
  { key: 'approved', label: 'Approved' },
  { key: 'sent', label: 'Sent' },
  { key: 'not_generated', label: 'Not generated', optional: true },
  { key: 'failed', label: 'Failed', optional: true },
];

const EMPTY_FILTER_TEXT = {
  draft: 'No draft minutes.',
  approved: 'No approved minutes waiting to be sent.',
  sent: 'No sent minutes yet.',
  not_generated: 'Every meeting has minutes.',
  failed: 'Nothing has failed.',
};

const SORTS = {
  newest: { label: 'Date (Newest)', fn: (a, b) => new Date(b.call_date) - new Date(a.call_date) },
  oldest: { label: 'Date (Oldest)', fn: (a, b) => new Date(a.call_date) - new Date(b.call_date) },
  name: { label: 'Name (A–Z)', fn: (a, b) => (a.session_name || '').localeCompare(b.session_name || '') },
};

const rowKey = (row) => row.transcript_id || row.mom?.id;

export default function MinutesPage() {
  const { isAdmin } = useAuth();
  // Only the project picked in the sidebar — another project's meetings never show here.
  const { projectId, project, loaded: projectsLoaded } = useProject();
  const navigate = useNavigate();
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));

  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);       // transcript_id or mom id being worked on
  const [confirmSend, setConfirmSend] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('newest');
  // undefined = auto (desktop shows the first readable meeting); null = user closed the panel.
  const [selectedKey, setSelectedKey] = useState(undefined);
  const [menu, setMenu] = useState(null);           // { anchor, row } for a card's ⋯ menu
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerChoice, setPickerChoice] = useState('');
  // Full MOM documents fetched by the detail panel, by mom id. Lives as long as the page.
  const docCache = useRef(new Map()).current;

  const notify = (message, severity = 'success') => setSnackbar({ open: true, message, severity });

  useEffect(() => { load(); setSelectedKey(undefined); }, [isAdmin, projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = async (showLoader = true) => {
    // No project selected (none exist yet) → show nothing rather than every project's minutes.
    if (!projectId) { setRows([]); return; }
    try {
      if (showLoader) setLoading(true);

      // Admins get the full meeting list (including ungenerated); everyone else gets the minutes.
      const [minutes, transcripts] = await Promise.all([
        momAPI.list(projectId),
        isAdmin ? momAPI.listTranscripts(projectId).catch(() => []) : Promise.resolve(null),
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
      docCache.delete(mom.id); // panel's Details tab must show the new sent date / recipients
      await load(false);
    } catch (err) {
      notify(err.response?.data?.detail || err.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const reviewTasks = (mom) => navigate(`/minutes/${mom.id}/tasks`);

  // ── derived list ──
  const withStatus = useMemo(() => rows.map((r) => ({ ...r, _status: statusOf(r) })), [rows]);
  const counts = useMemo(() => {
    const c = { all: withStatus.length };
    withStatus.forEach((r) => { c[r._status] = (c[r._status] || 0) + 1; });
    return c;
  }, [withStatus]);
  const visible = useMemo(() => {
    const list = filter === 'all' ? withStatus : withStatus.filter((r) => r._status === filter);
    return [...list].sort(SORTS[sort].fn);
  }, [withStatus, filter, sort]);

  // On desktop, until the user picks or closes, preview the first readable meeting in the
  // current filter + sort. Phones don't auto-open: there the panel would cover the whole list.
  const firstReadable = visible.find((r) => r.mom && !r.mom.generation_error);
  const effectiveKey = selectedKey === undefined
    ? (isDesktop && firstReadable ? rowKey(firstReadable) : null)
    : selectedKey;
  const selected = withStatus.find((r) => rowKey(r) === effectiveKey) || null;
  const ungenerated = withStatus.filter((r) => !r.mom);

  const openRow = (row) => {
    if (!row.mom) { if (isAdmin) generate(row.transcript_id); return; }
    setSelectedKey(rowKey(row));
  };

  const primaryLabel = (row) => {
    if (!row.mom) return 'Generate';
    if (isAdmin && row._status === 'draft') return 'Review Minutes';
    return 'View Minutes';
  };

  const emptyText = () => {
    if (projectsLoaded && !projectId) return 'No projects yet — an admin creates one in Admin → Customer Settings.';
    if (filter !== 'all') return EMPTY_FILTER_TEXT[filter];
    return isAdmin
      ? `No transcripts yet for ${project?.name || 'this project'}.`
      : `No minutes are available yet for ${project?.name || 'this project'}.`;
  };

  const sortSelect = (
    <Select
      size="small"
      value={sort}
      onChange={(e) => setSort(e.target.value)}
      inputProps={{ 'aria-label': 'Sort' }}
      sx={{ minWidth: '10rem', flexShrink: 0 }}
    >
      {Object.entries(SORTS).map(([k, s]) => <MenuItem key={k} value={k}>{s.label}</MenuItem>)}
    </Select>
  );

  const panel = selected && (
    <MinutesDetailPanel
      key={rowKey(selected)} // a different meeting starts on the Preview tab
      row={selected}
      statusKey={selected._status}
      projectName={project?.name}
      isAdmin={isAdmin}
      busy={busyId === selected.transcript_id || busyId === selected.mom?.id}
      cache={docCache}
      onClose={() => setSelectedKey(null)}
      onDownload={download}
      onRegenerate={generate}
      onSend={(mom) => setConfirmSend(mom)}
      onReviewTasks={reviewTasks}
    />
  );

  return (
    <Box sx={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* List column */}
      {/* Full width: the cards stretch with the window (no centred max-width gutter). */}
      <Box sx={{ flexGrow: 1, minWidth: 0, overflowY: 'auto', px: { xs: 2, md: 3 }, py: { xs: 2, md: 3 } }}>
        <Box>
          <PageHeader
            icon={<DescriptionOutlined />}
            title="Minutes of Meeting"
            subtitle="Review and share AI-generated meeting minutes from processed transcripts."
            actions={(
              <>
                {/* Readers have no filter pills, so the sort sits up here instead of on its own row. */}
                {!isAdmin && sortSelect}
                <Tooltip title="Refresh">
                  <IconButton onClick={() => load()} aria-label="Refresh"><Refresh /></IconButton>
                </Tooltip>
                {isAdmin && (
                  <Button variant="contained" startIcon={<Add />} onClick={() => { setPickerChoice(''); setPickerOpen(true); }}>
                    Generate Minutes
                  </Button>
                )}
              </>
            )}
          />

          {/* Toolbar (admin): filter pills + sort */}
          {isAdmin && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2 }}>
            <Box sx={{ display: 'flex', gap: 1, overflowX: 'auto', flexGrow: 1, pb: 0.5, '&::-webkit-scrollbar': { display: 'none' } }}>
              {FILTERS.filter((f) => !f.optional || counts[f.key]).map((f) => {
                const active = filter === f.key;
                return (
                  <Chip
                    key={f.key}
                    clickable
                    onClick={() => setFilter(f.key)}
                    label={(
                      <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
                        {f.label}
                        <Box component="span" sx={{
                          minWidth: '1.4rem', px: 0.6, borderRadius: 999, fontSize: '0.75rem', lineHeight: '1.4rem', textAlign: 'center',
                          bgcolor: active ? alpha(tokens.card, 0.2) : tokens.sageSoft,
                        }}>
                          {counts[f.key] || 0}
                        </Box>
                      </Box>
                    )}
                    sx={{
                      height: '2.25rem', px: 0.5, flexShrink: 0,
                      bgcolor: active ? 'primary.main' : 'background.paper',
                      color: active ? 'primary.contrastText' : 'text.primary',
                      border: 1, borderColor: active ? 'primary.main' : 'divider',
                      '&:hover': { bgcolor: active ? 'primary.dark' : tokens.sageSoft },
                    }}
                  />
                );
              })}
            </Box>
            {sortSelect}
          </Box>
          )}

          {/* Cards */}
          {loading ? (
            [0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={76} sx={{ mb: 1.5 }} />)
          ) : visible.length === 0 ? (
            <Paper variant="outlined" sx={{ p: 5, textAlign: 'center', color: 'text.secondary', borderRadius: 3 }}>
              {emptyText()}
            </Paper>
          ) : visible.map((row) => {
            const mom = row.mom;
            const isSelected = rowKey(row) === effectiveKey;
            const working = busyId === row.transcript_id || (mom && busyId === mom.id);
            const primary = primaryLabel(row);
            const isReview = primary === 'Review Minutes';
            return (
              <Paper
                key={rowKey(row)}
                variant="outlined"
                onClick={() => mom && openRow(row)}
                sx={{
                  display: 'flex', alignItems: 'center', gap: { xs: 1.5, sm: 2 }, p: { xs: 1.5, sm: 2 }, mb: 1.5, borderRadius: 3,
                  cursor: mom ? 'pointer' : 'default',
                  borderColor: isSelected ? 'secondary.main' : 'divider',
                  bgcolor: isSelected ? alpha(tokens.sage, 0.08) : 'background.paper',
                  transition: 'border-color .15s, background-color .15s',
                  '&:hover': mom ? { borderColor: isSelected ? 'secondary.main' : alpha(tokens.sage, 0.5) } : {},
                }}
              >
                <Box sx={{ width: '2.75rem', height: '2.75rem', borderRadius: 2, display: { xs: 'none', sm: 'grid' }, placeItems: 'center', bgcolor: tokens.sageLight, color: 'primary.main', flexShrink: 0 }}>
                  <Description fontSize="small" />
                </Box>

                <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                  <Typography sx={{ fontWeight: 600 }} noWrap title={row.session_name}>{row.session_name}</Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5, flexWrap: 'wrap' }}>
                    <Typography variant="body2" color="text.secondary">{formatCallDate(row.call_date) || '—'}</Typography>
                    {isAdmin && (
                      <Tooltip title={failureReason(mom)}>
                        <span><StatusChip status={STATUS[row._status].label} /></span>
                      </Tooltip>
                    )}
                    {mom?.truncated && (
                      <Tooltip title="The document hit the generation length limit and may be incomplete.">
                        <span><StatusChip status="draft" label="Truncated" /></span>
                      </Tooltip>
                    )}
                  </Box>
                </Box>

                {/* Actions must not also trigger the card's open click. */}
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                  {working ? <CircularProgress size={22} sx={{ mx: 2 }} /> : (
                    (mom || isAdmin) && (
                      <Button
                        variant={isReview ? 'contained' : 'outlined'}
                        endIcon={isReview ? <ArrowForward /> : null}
                        // View/Review opens the full document page; the card itself opens the preview panel.
                        onClick={() => (mom ? navigate(`/minutes/${mom.id}`) : openRow(row))}
                        sx={{ display: { xs: 'none', sm: 'inline-flex' }, whiteSpace: 'nowrap' }}
                      >
                        {primary}
                      </Button>
                    )
                  )}
                  {(mom || isAdmin) && (
                    <IconButton
                      aria-label={`More actions for ${row.session_name}`}
                      onClick={(e) => setMenu({ anchor: e.currentTarget, row })}
                      sx={{ border: 1, borderColor: 'divider', borderRadius: 2 }}
                    >
                      <MoreVert fontSize="small" />
                    </IconButton>
                  )}
                </Box>
              </Paper>
            );
          })}
        </Box>
      </Box>

      {/* Detail panel: docked column on desktop, full-screen drawer on phones */}
      {isDesktop ? (
        selected && (
          <Box sx={{ width: { md: '26rem', xl: '30rem' }, flexShrink: 0, borderLeft: 1, borderColor: 'divider', minHeight: 0 }}>
            {panel}
          </Box>
        )
      ) : (
        <Drawer anchor="right" open={!!selected} onClose={() => setSelectedKey(null)} sx={{ '& .MuiDrawer-paper': { width: '100%' } }}>
          {panel}
        </Drawer>
      )}

      {/* Card ⋯ menu — one level only (no nested menus). */}
      <Menu anchorEl={menu?.anchor} open={!!menu} onClose={() => setMenu(null)}>
        {menu && (() => {
          const { row } = menu;
          const mom = row.mom;
          const readable = mom && !mom.generation_error;
          const close = (fn) => () => { setMenu(null); fn(); };
          return [
            readable && (
              <MenuItem key="view" onClick={close(() => openRow(row))}>
                <ListItemIcon><DescriptionOutlined fontSize="small" /></ListItemIcon>Preview
              </MenuItem>
            ),
            readable && (
              <MenuItem key="dl" onClick={close(() => download(mom))}>
                <ListItemIcon><Download fontSize="small" /></ListItemIcon>Download Word
              </MenuItem>
            ),
            readable && (
              <MenuItem key="page" onClick={close(() => navigate(`/minutes/${mom.id}`))}>
                <ListItemIcon><OpenInNew fontSize="small" /></ListItemIcon>Open full page
              </MenuItem>
            ),
            isAdmin && row.transcript_id && (
              <MenuItem key="gen" onClick={close(() => generate(row.transcript_id))}>
                <ListItemIcon><Autorenew fontSize="small" /></ListItemIcon>{mom ? 'Regenerate' : 'Generate minutes'}
              </MenuItem>
            ),
            isAdmin && readable && (
              <MenuItem key="tasks" onClick={close(() => reviewTasks(mom))}>
                <ListItemIcon><PlaylistAddCheck fontSize="small" /></ListItemIcon>{mom.approved_at ? 'View tasks' : 'Review tasks & approve'}
              </MenuItem>
            ),
            isAdmin && readable && (
              <MenuItem key="send" onClick={close(() => setConfirmSend(mom))}>
                <ListItemIcon><Send fontSize="small" /></ListItemIcon>Send by email
              </MenuItem>
            ),
          ].filter(Boolean);
        })()}
      </Menu>

      {/* ── Generate picker (admin): meetings in this project that have no minutes yet. */}
      <Dialog open={pickerOpen} onClose={() => setPickerOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Generate minutes</DialogTitle>
        <DialogContent dividers>
          {ungenerated.length === 0 ? (
            <Typography color="text.secondary">
              Every meeting in {project?.name || 'this project'} already has minutes. Use Regenerate on a meeting to produce a new version.
            </Typography>
          ) : (
            <RadioGroup value={pickerChoice} onChange={(e) => setPickerChoice(e.target.value)}>
              {[...ungenerated].sort(SORTS.newest.fn).map((r) => (
                <FormControlLabel
                  key={r.transcript_id}
                  value={r.transcript_id}
                  control={<Radio />}
                  label={(
                    <Box>
                      <Typography sx={{ fontWeight: 500 }}>{r.session_name}</Typography>
                      <Typography variant="caption" color="text.secondary">{formatCallDate(r.call_date)}</Typography>
                    </Box>
                  )}
                  sx={{ py: 0.5 }}
                />
              ))}
            </RadioGroup>
          )}
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>
            Generation can take a minute or two on a long call.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPickerOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!pickerChoice}
            onClick={() => { setPickerOpen(false); generate(pickerChoice); }}
          >
            Generate
          </Button>
        </DialogActions>
      </Dialog>

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

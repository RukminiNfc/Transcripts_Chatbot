import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, Paper, Chip, CircularProgress, Alert, Tooltip, IconButton, Snackbar,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
  FormControl, InputLabel, Select, MenuItem, Menu, ListItemIcon, Skeleton, Drawer, useMediaQuery,
} from '@mui/material';
import { useTheme, alpha } from '@mui/material/styles';
import {
  Refresh, Download, MenuBookOutlined, MenuBook, History, CallMerge, DeleteOutline, MoreVert,
  OpenInNew, DescriptionOutlined, ExpandMore, ExpandLess,
} from '@mui/icons-material';
import { modulesAPI } from '../../services/api';
import { useAuth } from '../../auth/AuthContext';
import { useProject } from '../../projects/ProjectContext';
import PageHeader from '../../layout/PageHeader';
import ModuleDetailPanel from './ModuleDetailPanel';
import { tokens } from '../../theme/theme';

// Proposals shown before the "Show all" toggle.
const PROPOSALS_VISIBLE = 4;

const lastUpdated = (row) => {
  // The meeting that last changed it, not when the pipeline ran — after a backfill the latter is
  // the same day for every module.
  const when = row.last_call_date || row.last_synced_at;
  return when ? new Date(when).toLocaleDateString() : '';
};

/**
 * The project's requirement documents, one per product area.
 *
 * Open to every logged-in user, like Minutes: this is the written answer to "what did we agree
 * about Orders?", which is exactly what a non-admin asks the chatbot. Editing (on the document
 * page), proposal triage and the Requirements tab of the preview are admin-only.
 *
 * Scoped to the project picked in the sidebar, like Minutes — without customer_id the API returns
 * every project's modules mixed together.
 *
 * Modules with no content are shown rather than hidden. An empty module is a real statement —
 * nothing has been agreed about it yet — and dropping those rows would make the taxonomy look
 * smaller than it is, which matters when deciding whether a meeting was filed correctly.
 */
export default function ModulesPage() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const { projectId, project, loaded: projectsLoaded } = useProject();
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));
  // Below sm the card's Open button is hidden, so the ⋯ menu carries "Open full page" instead.
  const isPhone = useMediaQuery(theme.breakpoints.down('sm'));

  const [rows, setRows] = useState([]);
  const [proposed, setProposed] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });
  // undefined = auto (desktop previews the first module with content); null = user closed the panel.
  const [selectedId, setSelectedId] = useState(undefined);
  const [menu, setMenu] = useState(null);                 // { anchor, row } for a module card's ⋯
  const [proposalMenu, setProposalMenu] = useState(null); // { anchor, proposal } for a proposal's ⋯
  const [showAllProposals, setShowAllProposals] = useState(false);
  // Documents, revisions and requirements fetched by the detail panel. Cleared on every reload so
  // an approve/merge (which rewrites a target module) never shows a stale document.
  const panelCache = useRef(new Map()).current;

  // Merge Dialog states
  const [mergeDialogOpen, setMergeDialogOpen] = useState(false);
  const [selectedProposal, setSelectedProposal] = useState(null);
  const [targetModuleId, setTargetModuleId] = useState('');
  const [mergeBusy, setMergeBusy] = useState(false);

  const notify = (message, severity = 'success') => setSnackbar({ open: true, message, severity });

  useEffect(() => { load(); setSelectedId(undefined); }, [isAdmin, projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = async (showLoader = true) => {
    panelCache.clear();
    // No project selected (none exist yet) → show nothing rather than every project's modules.
    if (!projectId) { setRows([]); setProposed([]); return; }
    try {
      if (showLoader) setLoading(true);
      // Proposed modules are an admin concern — a normal user can do nothing about one.
      const [active, waiting] = await Promise.all([
        modulesAPI.list({ customer_id: projectId }),
        isAdmin ? modulesAPI.list({ customer_id: projectId, status: 'proposed' }).catch(() => []) : Promise.resolve([]),
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

  const openPage = (row) => navigate(`/documents/${row.id}`);
  const openHistory = (row) => navigate(`/documents/${row.id}?tab=history`);

  const withContent = rows.filter((r) => r.has_content).length;
  const totalRequirements = rows.reduce((sum, r) => sum + (r.requirement_count || 0), 0);

  // On desktop, until the user picks or closes, preview the first module with content. Phones
  // don't auto-open: there the panel would cover the whole list.
  const firstReadable = rows.find((r) => r.has_content);
  const effectiveId = selectedId === undefined
    ? (isDesktop && firstReadable ? firstReadable.id : null)
    : selectedId;
  const selected = rows.find((r) => r.id === effectiveId) || null;

  const visibleProposals = showAllProposals ? proposed : proposed.slice(0, PROPOSALS_VISIBLE);

  const emptyText = () => {
    if (projectsLoaded && !projectId) return 'No projects yet — an admin creates one in Project Configuration → Projects.';
    return `No modules are defined for ${project?.name || 'this project'} yet.`;
  };

  const panel = selected && (
    <ModuleDetailPanel
      key={selected.id} // a different module starts on the Document tab
      row={selected}
      projectId={projectId}
      projectName={project?.name}
      isAdmin={isAdmin}
      busy={busyId === selected.id}
      cache={panelCache}
      onClose={() => setSelectedId(null)}
      onDownload={download}
      onOpen={openPage}
      onHistory={openHistory}
    />
  );

  return (
    <Box sx={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* List column */}
      <Box sx={{ flexGrow: 1, minWidth: 0, overflowY: 'auto', px: { xs: 2, md: 3 }, py: { xs: 2, md: 3 } }}>
        <PageHeader
          icon={<MenuBookOutlined />}
          title="Requirement Documents"
          subtitle="One living document per product area, updated after each grooming call."
          actions={(
            <Tooltip title="Refresh">
              <IconButton onClick={() => load()} aria-label="Refresh"><Refresh /></IconButton>
            </Tooltip>
          )}
        />

        {/* Proposed modules (admin) */}
        {proposed.length > 0 && (
          <Paper variant="outlined" sx={{ mb: 3, p: { xs: 1.5, sm: 2 }, borderRadius: 3, bgcolor: tokens.sageSoft }}>
            <Typography sx={{ fontWeight: 600 }}>
              {proposed.length} proposed module{proposed.length > 1 ? 's' : ''} awaiting review
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              Recent meetings discussed areas that don't fit an existing module. Approve one as a new
              module, or merge it into an existing one.
            </Typography>

            {visibleProposals.map((m) => {
              const working = busyId === m.id;
              return (
                <Paper
                  key={m.id}
                  variant="outlined"
                  sx={{ display: 'flex', alignItems: 'center', gap: { xs: 1, sm: 2 }, p: 1.5, mb: 1, borderRadius: 2 }}
                >
                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{m.module_name}</Typography>
                    {m.description && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {m.description}
                      </Typography>
                    )}
                  </Box>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0 }}>
                    {working ? <CircularProgress size={22} sx={{ mx: 2 }} /> : (
                      <Button variant="contained" size="small" onClick={() => approve(m)} sx={{ whiteSpace: 'nowrap' }}>
                        Approve as New
                      </Button>
                    )}
                    <IconButton
                      aria-label={`More actions for ${m.module_name}`}
                      disabled={working}
                      onClick={(e) => setProposalMenu({ anchor: e.currentTarget, proposal: m })}
                      sx={{ border: 1, borderColor: 'divider', borderRadius: 2 }}
                    >
                      <MoreVert fontSize="small" />
                    </IconButton>
                  </Box>
                </Paper>
              );
            })}

            {proposed.length > PROPOSALS_VISIBLE && (
              <Button
                size="small"
                onClick={() => setShowAllProposals((v) => !v)}
                endIcon={showAllProposals ? <ExpandLess /> : <ExpandMore />}
              >
                {showAllProposals ? 'Show less' : `Show all (${proposed.length})`}
              </Button>
            )}
          </Paper>
        )}

        {/* Module cards */}
        {loading ? (
          [0, 1, 2, 3].map((i) => <Skeleton key={i} variant="rounded" height={76} sx={{ mb: 1.5 }} />)
        ) : rows.length === 0 ? (
          <Paper variant="outlined" sx={{ p: 5, textAlign: 'center', color: 'text.secondary', borderRadius: 3 }}>
            {emptyText()}
          </Paper>
        ) : rows.map((row) => {
          const isSelected = row.id === effectiveId;
          const working = busyId === row.id;
          const readable = row.has_content;
          const updated = lastUpdated(row);
          return (
            <Paper
              key={row.id}
              variant="outlined"
              onClick={() => readable && setSelectedId(row.id)}
              sx={{
                display: 'flex', alignItems: 'center', gap: { xs: 1.5, sm: 2 }, p: { xs: 1.5, sm: 2 }, mb: 1.5, borderRadius: 3,
                cursor: readable ? 'pointer' : 'default',
                borderColor: isSelected ? 'secondary.main' : 'divider',
                bgcolor: isSelected ? alpha(tokens.sage, 0.08) : 'background.paper',
                transition: 'border-color .15s, background-color .15s',
                '&:hover': readable ? { borderColor: isSelected ? 'secondary.main' : alpha(tokens.sage, 0.5) } : {},
              }}
            >
              <Box sx={{
                width: '2.75rem', height: '2.75rem', borderRadius: 2, display: { xs: 'none', sm: 'grid' }, placeItems: 'center', flexShrink: 0,
                bgcolor: readable ? tokens.sageLight : tokens.borderLight, color: readable ? 'primary.main' : 'text.disabled',
              }}>
                <MenuBook fontSize="small" />
              </Box>

              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography sx={{ fontWeight: 600 }} noWrap title={row.module_name}>{row.module_name}</Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5, flexWrap: 'wrap' }}>
                  {readable ? (
                    <>
                      <Chip size="small" label={row.current_version} />
                      <Typography variant="body2" color="text.secondary">
                        {[
                          isAdmin && `${row.requirement_count || 0} requirements`,
                          `${row.revision_count || 0} revisions`,
                          updated && `Updated ${updated}`,
                        ].filter(Boolean).join(' · ')}
                      </Typography>
                    </>
                  ) : (
                    <>
                      <Chip size="small" label="No content yet" variant="outlined" />
                      {row.description && (
                        <Typography variant="body2" color="text.secondary" noWrap title={row.description} sx={{ minWidth: 0, flex: 1 }}>
                          {row.description}
                        </Typography>
                      )}
                    </>
                  )}
                </Box>
              </Box>

              {/* Actions must not also trigger the card's preview click. */}
              {readable && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                  {working ? <CircularProgress size={22} sx={{ mx: 2 }} /> : (
                    <Button
                      variant="outlined"
                      onClick={() => openPage(row)}
                      sx={{ display: { xs: 'none', sm: 'inline-flex' }, whiteSpace: 'nowrap' }}
                    >
                      Open
                    </Button>
                  )}
                  <IconButton
                    aria-label={`More actions for ${row.module_name}`}
                    onClick={(e) => setMenu({ anchor: e.currentTarget, row })}
                    sx={{ border: 1, borderColor: 'divider', borderRadius: 2 }}
                  >
                    <MoreVert fontSize="small" />
                  </IconButton>
                </Box>
              )}
            </Paper>
          );
        })}

        {!loading && rows.length > 0 && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
            {withContent} of {rows.length} modules have content
            {isAdmin && ` · ${totalRequirements} requirements indexed for chat`}
          </Typography>
        )}
      </Box>

      {/* Detail panel: docked column on desktop, full-screen drawer on phones */}
      {isDesktop ? (
        selected && (
          <Box sx={{ width: { md: '26rem', xl: '30rem' }, flexShrink: 0, borderLeft: 1, borderColor: 'divider', minHeight: 0 }}>
            {panel}
          </Box>
        )
      ) : (
        <Drawer anchor="right" open={!!selected} onClose={() => setSelectedId(null)} sx={{ '& .MuiDrawer-paper': { width: '100%' } }}>
          {panel}
        </Drawer>
      )}

      {/* Module card ⋯ menu — one level only (no nested menus). */}
      <Menu anchorEl={menu?.anchor} open={!!menu} onClose={() => setMenu(null)}>
        {menu && (() => {
          const { row } = menu;
          const close = (fn) => () => { setMenu(null); fn(); };
          return [
            <MenuItem key="preview" onClick={close(() => setSelectedId(row.id))}>
              <ListItemIcon><DescriptionOutlined fontSize="small" /></ListItemIcon>Preview
            </MenuItem>,
            isPhone && (
              <MenuItem key="page" onClick={close(() => openPage(row))}>
                <ListItemIcon><OpenInNew fontSize="small" /></ListItemIcon>Open full page
              </MenuItem>
            ),
            <MenuItem key="history" onClick={close(() => openHistory(row))}>
              <ListItemIcon><History fontSize="small" /></ListItemIcon>Version history
            </MenuItem>,
            <MenuItem key="dl" onClick={close(() => download(row))}>
              <ListItemIcon><Download fontSize="small" /></ListItemIcon>Download Word
            </MenuItem>,
          ].filter(Boolean);
        })()}
      </Menu>

      {/* Proposal ⋯ menu */}
      <Menu anchorEl={proposalMenu?.anchor} open={!!proposalMenu} onClose={() => setProposalMenu(null)}>
        {proposalMenu && (() => {
          const { proposal } = proposalMenu;
          const close = (fn) => () => { setProposalMenu(null); fn(); };
          return [
            <MenuItem key="merge" onClick={close(() => openMergeDialog(proposal))}>
              <ListItemIcon><CallMerge fontSize="small" /></ListItemIcon>Merge into existing…
            </MenuItem>,
            <MenuItem key="dismiss" onClick={close(() => dismiss(proposal))} sx={{ color: 'error.main' }}>
              <ListItemIcon><DeleteOutline fontSize="small" color="error" /></ListItemIcon>Dismiss
            </MenuItem>,
          ];
        })()}
      </Menu>

      {/* Merge Dialog */}
      <Dialog
        open={mergeDialogOpen}
        onClose={() => !mergeBusy && setMergeDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
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
          <Button onClick={() => setMergeDialogOpen(false)} disabled={mergeBusy} color="inherit">
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

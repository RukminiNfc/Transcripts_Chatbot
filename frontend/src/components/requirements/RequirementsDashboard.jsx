import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, Paper, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Chip, Select, MenuItem, Menu, ListItemIcon, ListItemText, IconButton, TextField, InputAdornment,
  Skeleton, Snackbar, Alert, ButtonBase, useMediaQuery,
} from '@mui/material';
import { useTheme, alpha } from '@mui/material/styles';
import {
  FactCheckOutlined, FileDownloadOutlined, Search, MoreVert, ContentCopy, ChatBubbleOutline,
  FolderOutlined, TableChartOutlined, DescriptionOutlined, DataObject, KeyboardArrowDown,
} from '@mui/icons-material';
import api, { API_ORIGIN } from '../../services/api';
import { useProject } from '../../projects/ProjectContext';
import PageHeader from '../../layout/PageHeader';
import CategoryChip from '../common/CategoryChip';
import { formatCallDate } from '../minutes/momParse';
import { tokens } from '../../theme/theme';

/**
 * Requirements for the project selected in the sidebar (the header picker changes the same
 * project). Filtering, search and the Excel/CSV export all run client-side over the loaded list;
 * JSON export is the backend's consolidated export.
 */

const RECENT_DAYS = 7;
const ALL = '';

const isRecent = (r) => {
  const d = new Date(r.updated_at);
  return !isNaN(d) && (Date.now() - d) / 86400000 <= RECENT_DAYS;
};

const fmtDate = (v) => formatCallDate(v, { month: 'numeric', day: 'numeric', year: 'numeric' }) || '—';

// One flat record per requirement — shared by the CSV and Excel exports.
const exportRows = (list) => list.map((r) => ({
  Category: r.category || 'General',
  'Sub-Category': r.sub_category || '',
  Requirement: r.current_text || '',
  'Transcript Date': fmtDate(r.transcript_date),
  'Last Updated': fmtDate(r.updated_at),
}));

const saveBlob = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

// Filter dropdown styled as a pill, matching the "All" / "Recently Updated" pills.
function PillSelect({ value, onChange, allLabel, options, disabled }) {
  return (
    <Select
      size="small"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      displayEmpty
      disabled={disabled}
      IconComponent={KeyboardArrowDown}
      renderValue={(v) => v || allLabel}
      inputProps={{ 'aria-label': allLabel }}
      sx={{
        height: '2.25rem', borderRadius: 999, minWidth: '9rem', maxWidth: '16rem', flexShrink: 0,
        bgcolor: value ? tokens.sageLight : 'background.paper',
        '& .MuiSelect-select': { py: 0, fontSize: '0.875rem' },
      }}
    >
      <MenuItem value={ALL}>{allLabel}</MenuItem>
      {options.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
    </Select>
  );
}

export default function RequirementsDashboard() {
  const { projects, projectId, project, setProjectId } = useProject();
  const navigate = useNavigate();
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));

  const [requirements, setRequirements] = useState([]);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState('all');           // 'all' | 'recent'
  const [category, setCategory] = useState(ALL);
  const [subCategory, setSubCategory] = useState(ALL);
  const [search, setSearch] = useState('');
  const [exportAnchor, setExportAnchor] = useState(null);
  const [projectAnchor, setProjectAnchor] = useState(null);
  const [rowMenu, setRowMenu] = useState(null);      // { anchor, req }
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

  const notify = (message, severity = 'success') => setSnackbar({ open: true, message, severity });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!projectId) { setRequirements([]); return; }
      setLoading(true);
      try {
        const resp = await api.get(`${API_ORIGIN}/requirements/customer/${projectId}`);
        if (!cancelled) setRequirements(resp.data || []);
      } catch (err) {
        if (!cancelled) { setRequirements([]); notify(`Could not load requirements: ${err.message}`, 'error'); }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId]);

  // Reset filters when the project changes — another project's categories won't exist.
  const changeProject = (id) => { setCategory(ALL); setSubCategory(ALL); setView('all'); setProjectId(id); };

  // ── derived ──
  const recentCount = useMemo(() => requirements.filter(isRecent).length, [requirements]);

  const categories = useMemo(
    () => [...new Set(requirements.map((r) => r.category || 'General'))].sort((a, b) => a.localeCompare(b)),
    [requirements],
  );
  const subCategories = useMemo(
    () => [...new Set(requirements
      .filter((r) => !category || (r.category || 'General') === category)
      .map((r) => r.sub_category)
      .filter(Boolean))].sort((a, b) => a.localeCompare(b)),
    [requirements, category],
  );

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = requirements.filter((r) => {
      if (view === 'recent' && !isRecent(r)) return false;
      if (category && (r.category || 'General') !== category) return false;
      if (subCategory && r.sub_category !== subCategory) return false;
      if (q && ![r.current_text, r.category, r.sub_category].some((v) => (v || '').toLowerCase().includes(q))) return false;
      return true;
    });
    // Recently Updated reads newest-first; otherwise group by category, then sub-category.
    return list.sort(view === 'recent'
      ? (a, b) => new Date(b.updated_at) - new Date(a.updated_at)
      : (a, b) => (a.category || '').localeCompare(b.category || '') || (a.sub_category || '').localeCompare(b.sub_category || ''));
  }, [requirements, view, category, subCategory, search]);

  const filtered = view !== 'all' || category || subCategory || search.trim();

  // ── export ──
  const fileStem = `Requirements_${(project?.name || 'project').replace(/[^A-Za-z0-9_-]+/g, '_')}_${new Date().toISOString().slice(0, 10)}`;

  const exportCsv = () => {
    const rows = exportRows(visible);
    const headers = Object.keys(rows[0] || exportRows([{}])[0]);
    const esc = (v) => `"${String(v).replace(/"/g, '""')}"`;
    const csv = [headers.map(esc).join(','), ...rows.map((r) => headers.map((h) => esc(r[h])).join(','))].join('\r\n');
    // BOM so Excel opens the UTF-8 file with the right characters (dashes, quotes, accents).
    saveBlob(new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' }), `${fileStem}.csv`);
  };

  const exportXlsx = async () => {
    // Loaded on demand: SheetJS is large and only needed at export time.
    const XLSX = await import('xlsx');
    const ws = XLSX.utils.json_to_sheet(exportRows(visible));
    ws['!cols'] = [{ wch: 24 }, { wch: 28 }, { wch: 90 }, { wch: 15 }, { wch: 15 }];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Requirements');
    XLSX.writeFile(wb, `${fileStem}.xlsx`);
  };

  // The backend's consolidated export — always the full active list, not the filtered view.
  const exportJson = async () => {
    const resp = await api.get(`${API_ORIGIN}/requirements/customer/${projectId}/export`);
    saveBlob(new Blob([JSON.stringify(resp.data, null, 2)], { type: 'application/json' }), `${fileStem}.json`);
  };

  const runExport = async (fn) => {
    setExportAnchor(null);
    try { await fn(); } catch (err) { notify(`Export failed: ${err.message}`, 'error'); }
  };

  // ── row actions ──
  const copyRequirement = async (req) => {
    try {
      await navigator.clipboard.writeText(req.current_text || '');
      notify('Requirement copied.');
    } catch {
      notify('Could not copy to the clipboard.', 'error');
    }
  };

  const askInChat = (req) => navigate('/', {
    state: { prefill: `What was discussed about this requirement, and has it changed over time? "${req.current_text}"` },
  });

  const emptyText = !projectId
    ? 'No projects yet — an admin creates one in Project Configuration → Projects.'
    : filtered
      ? 'No requirements match these filters.'
      : 'No active requirements yet. Upload a transcript on the Transcripts page to extract them.';

  const rowMenuButton = (req) => (
    <IconButton
      aria-label="Requirement actions"
      onClick={(e) => setRowMenu({ anchor: e.currentTarget, req })}
      sx={{ border: 1, borderColor: 'divider', borderRadius: 2 }}
    >
      <MoreVert fontSize="small" />
    </IconButton>
  );

  const pill = (key, label, count) => {
    const active = view === key;
    return (
      <Chip
        clickable
        onClick={() => setView(key)}
        label={(
          <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
            {label}
            {count !== undefined && (
              <Box component="span" sx={{
                minWidth: '1.4rem', px: 0.6, borderRadius: 999, fontSize: '0.75rem', lineHeight: '1.4rem', textAlign: 'center',
                bgcolor: active ? alpha(tokens.card, 0.2) : tokens.sageSoft,
              }}>
                {count}
              </Box>
            )}
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
  };

  return (
    <Box sx={{ px: { xs: 2, md: 3 }, py: { xs: 2, md: 3 } }}>
      <PageHeader
        icon={<FactCheckOutlined />}
        title="Requirements"
        subtitle="Track and manage extracted requirements across your project."
        actions={projects.length > 0 && (
          // Same project as the sidebar picker — changing either changes both. A plain button +
          // menu (not a Select) so icon, label/name and chevron share one vertical centre line.
          <ButtonBase
            onClick={(e) => setProjectAnchor(e.currentTarget)}
            aria-label={`Project: ${project?.name || 'none'}. Change project`}
            aria-haspopup="menu"
            sx={{
              display: 'flex', alignItems: 'center', gap: 1.5, height: '3.25rem', pl: 1, pr: 1.5,
              // 8px corners — same shape as the Export button (theme MuiButton borderRadius).
              minWidth: '15rem', border: 1, borderColor: 'divider', borderRadius: '8px',
              bgcolor: 'background.paper', textAlign: 'left',
              '&:hover': { borderColor: 'secondary.main' },
            }}
          >
            <Box sx={{ width: '2.25rem', height: '2.25rem', borderRadius: '6px', display: 'grid', placeItems: 'center', bgcolor: tokens.sageLight, color: 'primary.main', flexShrink: 0 }}>
              <FolderOutlined fontSize="small" />
            </Box>
            <Box sx={{ flexGrow: 1, minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', lineHeight: 1.2 }}>Project</Typography>
              <Typography sx={{ fontWeight: 600, lineHeight: 1.3 }} noWrap>{project?.name || 'Select a project'}</Typography>
            </Box>
            <KeyboardArrowDown fontSize="small" sx={{ color: 'text.secondary', flexShrink: 0 }} />
          </ButtonBase>
        )}
      />

      {/* Toolbar: view pills + category filters on the left, search + export on the right */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', mb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, overflowX: 'auto', pb: 0.5, flexGrow: 1, '&::-webkit-scrollbar': { display: 'none' } }}>
          {pill('all', 'All', requirements.length)}
          {pill('recent', 'Recently Updated', recentCount)}
          <PillSelect
            value={category}
            onChange={(v) => { setCategory(v); setSubCategory(ALL); }}
            allLabel="Category"
            options={categories}
            disabled={categories.length === 0}
          />
          <PillSelect
            value={subCategory}
            onChange={setSubCategory}
            allLabel="Sub-Category"
            options={subCategories}
            disabled={subCategories.length === 0}
          />
        </Box>
        <Box sx={{ display: 'flex', gap: 1, width: { xs: '100%', sm: 'auto' } }}>
          <TextField
            size="small"
            placeholder="Search requirements..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{ flexGrow: 1, minWidth: { sm: '15rem' } }}
            slotProps={{
              input: { startAdornment: <InputAdornment position="start"><Search fontSize="small" /></InputAdornment> },
              htmlInput: { 'aria-label': 'Search requirements' },
            }}
          />
          <Button
            variant="outlined"
            startIcon={<FileDownloadOutlined />}
            endIcon={<KeyboardArrowDown />}
            disabled={!projectId || requirements.length === 0}
            onClick={(e) => setExportAnchor(e.currentTarget)}
            sx={{ flexShrink: 0 }}
          >
            Export
          </Button>
        </Box>
      </Box>

      {/* List: table on desktop, cards on phones */}
      {loading ? (
        [0, 1, 2, 3, 4].map((i) => <Skeleton key={i} variant="rounded" height={56} sx={{ mb: 1 }} />)
      ) : visible.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 5, textAlign: 'center', color: 'text.secondary', borderRadius: 3 }}>{emptyText}</Paper>
      ) : isDesktop ? (
        <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 3 }}>
          <Table size="small" sx={{ '& td': { py: 1.25, verticalAlign: 'middle' } }}>
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: '15%' }}>Category</TableCell>
                <TableCell sx={{ width: '15%' }}>Sub-Category</TableCell>
                <TableCell>Requirement Statement</TableCell>
                <TableCell sx={{ width: '9rem', whiteSpace: 'nowrap' }}>Transcript Date</TableCell>
                <TableCell sx={{ width: '9rem', whiteSpace: 'nowrap' }}>Last Updated</TableCell>
                <TableCell sx={{ width: '4rem' }} aria-label="Actions" />
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map((req) => (
                <TableRow key={req.id} hover>
                  <TableCell><CategoryChip category={req.category || 'General'} /></TableCell>
                  <TableCell>{req.sub_category || '—'}</TableCell>
                  <TableCell>{req.current_text}</TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmtDate(req.transcript_date)}</TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmtDate(req.updated_at)}</TableCell>
                  <TableCell align="right">{rowMenuButton(req)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      ) : (
        visible.map((req) => (
          <Paper key={req.id} variant="outlined" sx={{ p: 2, mb: 1.5, borderRadius: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mb: 1 }}>
              <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1 }}>
                <CategoryChip category={req.category || 'General'} />
                {req.sub_category && <Typography variant="body2" color="text.secondary">{req.sub_category}</Typography>}
              </Box>
              {rowMenuButton(req)}
            </Box>
            <Typography variant="body2" sx={{ mb: 1 }}>{req.current_text}</Typography>
            <Typography variant="caption" color="text.secondary">
              Transcript {fmtDate(req.transcript_date)} · Updated {fmtDate(req.updated_at)}
            </Typography>
          </Paper>
        ))
      )}

      {!loading && visible.length > 0 && filtered && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
          Showing {visible.length} of {requirements.length} requirements.
        </Typography>
      )}

      {/* Project menu (header card) */}
      <Menu
        anchorEl={projectAnchor}
        open={!!projectAnchor}
        onClose={() => setProjectAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { minWidth: projectAnchor?.offsetWidth } } }}
      >
        {projects.map((p) => (
          <MenuItem
            key={p.id}
            selected={p.id === projectId}
            onClick={() => { setProjectAnchor(null); if (p.id !== projectId) changeProject(p.id); }}
          >
            {p.name}
          </MenuItem>
        ))}
      </Menu>

      {/* Export menu */}
      <Menu anchorEl={exportAnchor} open={!!exportAnchor} onClose={() => setExportAnchor(null)}>
        <MenuItem onClick={() => runExport(exportXlsx)}>
          <ListItemIcon><TableChartOutlined fontSize="small" /></ListItemIcon>
          <ListItemText primary="Excel (.xlsx)" secondary={filtered ? `${visible.length} filtered rows` : 'All rows'} />
        </MenuItem>
        <MenuItem onClick={() => runExport(exportCsv)}>
          <ListItemIcon><DescriptionOutlined fontSize="small" /></ListItemIcon>
          <ListItemText primary="CSV" secondary={filtered ? `${visible.length} filtered rows` : 'All rows'} />
        </MenuItem>
        <MenuItem onClick={() => runExport(exportJson)}>
          <ListItemIcon><DataObject fontSize="small" /></ListItemIcon>
          <ListItemText primary="JSON" secondary="Full consolidated export" />
        </MenuItem>
      </Menu>

      {/* Row ⋯ menu — one level only */}
      <Menu anchorEl={rowMenu?.anchor} open={!!rowMenu} onClose={() => setRowMenu(null)}>
        <MenuItem onClick={() => { const r = rowMenu.req; setRowMenu(null); copyRequirement(r); }}>
          <ListItemIcon><ContentCopy fontSize="small" /></ListItemIcon>
          Copy requirement
        </MenuItem>
        <MenuItem onClick={() => { const r = rowMenu.req; setRowMenu(null); askInChat(r); }}>
          <ListItemIcon><ChatBubbleOutline fontSize="small" /></ListItemIcon>
          Ask in Chat
        </MenuItem>
      </Menu>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
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

import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, IconButton, Skeleton, Tab, Tabs, Typography,
} from '@mui/material';
import { Close, Download, MenuBookOutlined, OpenInNew, History } from '@mui/icons-material';
import MarkdownRenderer from '../chat/MarkdownRenderer';
import api, { API_ORIGIN, modulesAPI } from '../../services/api';
import { tokens } from '../../theme/theme';

const fmtDate = (v) => (v ? new Date(v).toLocaleDateString() : '');

// How many revisions the History tab lists before pointing at the full page.
const RECENT_REVISIONS = 8;

/**
 * Right-hand panel for one module document (or a full-screen drawer on phones).
 *
 * Fetches into `cache` (a Map owned by ModulesPage, keyed `doc:<id>`, `rev:<id>`, `req:<projectId>`)
 * so flipping between cards doesn't refetch. Same no-"cancelled"-flag reasoning as
 * MinutesDetailPanel: results are keyed by id, so a late response can never land on the wrong
 * module, and the landing fetch must always re-render or a re-run effect leaves skeletons stuck.
 *
 * Requirements are admin-only, like the Requirements page they come from: the tab is not rendered
 * for a reader, and /requirements/customer is admin-gated on the server regardless. The list is the
 * project's requirements filtered by `category`, which is where the indexer writes the module name —
 * the same key the card's requirement count uses, so the two cannot disagree.
 */
export default function ModuleDetailPanel({
  row, projectId, projectName, isAdmin, busy, cache, onClose, onDownload, onOpen, onHistory,
}) {
  const moduleId = row?.id;
  const [tab, setTab] = useState('document');
  const [, rerender] = useState(0);
  const [errors, setErrors] = useState({});

  const fill = (key, fetcher, message) => {
    if (cache.has(key)) return;
    cache.set(key, null); // in flight — stops a re-run from firing a second request
    fetcher()
      .then((data) => { cache.set(key, data); rerender((n) => n + 1); })
      .catch((err) => {
        cache.delete(key);
        setErrors((e) => ({ ...e, [key]: err.response?.data?.detail || message }));
      });
  };

  const docKey = `doc:${moduleId}`;
  const revKey = `rev:${moduleId}`;
  const reqKey = `req:${projectId}`;

  useEffect(() => {
    if (!moduleId) return;
    fill(docKey, () => modulesAPI.get(moduleId), 'Could not load this document.');
  }, [moduleId, row]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!moduleId || tab !== 'history') return;
    fill(revKey, () => modulesAPI.revisions(moduleId), 'Could not load the history.');
  }, [moduleId, tab]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isAdmin || !projectId || tab !== 'requirements') return;
    fill(
      reqKey,
      () => api.get(`${API_ORIGIN}/requirements/customer/${projectId}`).then((r) => r.data || []),
      'Could not load requirements.',
    );
  }, [isAdmin, projectId, tab]); // eslint-disable-line react-hooks/exhaustive-deps

  const module = cache.get(docKey) || null;
  const revisions = cache.get(revKey) || null;
  const projectReqs = cache.get(reqKey) || null;
  const md = module?.content_markdown || '';

  // Grouped by sub-category (the document's sub-heading), in first-seen order.
  const reqGroups = useMemo(() => {
    if (!projectReqs || !row) return null;
    const groups = new Map();
    projectReqs
      .filter((r) => r.category === row.module_name)
      .forEach((r) => {
        const key = r.sub_category || 'General';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(r);
      });
    return [...groups.entries()];
  }, [projectReqs, row]);
  const reqCount = reqGroups ? reqGroups.reduce((n, [, items]) => n + items.length, 0) : row?.requirement_count;

  const skeleton = (
    <Box>
      <Skeleton variant="text" width="50%" sx={{ mb: 1 }} />
      <Skeleton variant="text" /><Skeleton variant="text" /><Skeleton variant="text" width="70%" />
      <Skeleton variant="rounded" height={64} sx={{ mt: 2 }} />
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, bgcolor: 'background.paper' }}>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5, px: 2.5, pt: 2.5, pb: 1.5 }}>
        <Box sx={{ width: '2.75rem', height: '2.75rem', borderRadius: 2, display: 'grid', placeItems: 'center', bgcolor: tokens.sageLight, color: 'primary.main', flexShrink: 0 }}>
          <MenuBookOutlined />
        </Box>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography variant="h4" component="h2" sx={{ fontSize: '1.2rem', wordBreak: 'break-word' }}>
              {row?.module_name}
            </Typography>
            {row?.current_version && <Chip size="small" label={row.current_version} />}
          </Box>
          <Typography variant="caption" color="text.secondary">
            {[
              isAdmin && `${row?.requirement_count || 0} requirements`,
              `${row?.revision_count || 0} revisions`,
              projectName,
            ].filter(Boolean).join('  |  ')}
          </Typography>
        </Box>
        <IconButton onClick={onClose} aria-label="Close panel" size="small"><Close /></IconButton>
      </Box>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="fullWidth" sx={{ borderBottom: 1, borderColor: 'divider', minHeight: '2.75rem', '& .MuiTab-root': { minHeight: '2.75rem' } }}>
        <Tab value="document" label="Document" />
        {isAdmin && <Tab value="requirements" label="Requirements" />}
        <Tab value="history" label="History" />
      </Tabs>

      {/* Body */}
      <Box sx={{ flexGrow: 1, minHeight: 0, overflowY: 'auto', px: 2.5, py: 2 }}>
        {tab === 'document' && (
          errors[docKey] ? <Alert severity="error">{errors[docKey]}</Alert>
            : !module ? skeleton
              : md ? <MarkdownRenderer content={md} />
                : <Typography color="text.secondary">Nothing has been recorded for this module yet.</Typography>
        )}

        {tab === 'requirements' && isAdmin && (
          errors[reqKey] ? <Alert severity="error">{errors[reqKey]}</Alert>
            : !reqGroups ? skeleton
              : reqGroups.length === 0 ? (
                <Typography color="text.secondary">No requirements are indexed for this module.</Typography>
              ) : (
                <>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
                    {reqCount} requirement{reqCount === 1 ? '' : 's'} in {reqGroups.length} section{reqGroups.length === 1 ? '' : 's'}
                  </Typography>
                  {reqGroups.map(([section, items]) => (
                    <Box key={section} sx={{ mb: 2.5 }}>
                      <Typography variant="subtitle2" sx={{ mb: 1 }}>
                        {section}{' '}
                        <Typography component="span" variant="caption" color="text.secondary">({items.length})</Typography>
                      </Typography>
                      {items.map((r) => (
                        <Box key={r.id} sx={{ p: 1.5, mb: 1, border: 1, borderColor: 'divider', borderRadius: 2, bgcolor: tokens.sageSoft }}>
                          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{r.current_text}</Typography>
                          {(r.transcript_date || r.updated_at) && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                              {r.transcript_date ? `Discussed ${fmtDate(r.transcript_date)}` : `Updated ${fmtDate(r.updated_at)}`}
                            </Typography>
                          )}
                        </Box>
                      ))}
                    </Box>
                  ))}
                </>
              )
        )}

        {tab === 'history' && (
          errors[revKey] ? <Alert severity="error">{errors[revKey]}</Alert>
            : !revisions ? skeleton
              : revisions.length === 0 ? (
                <Typography color="text.secondary">No revisions yet.</Typography>
              ) : (
                <>
                  {revisions.slice(0, RECENT_REVISIONS).map((r) => (
                    <Box
                      key={r.id}
                      onClick={() => onHistory(row)}
                      sx={{ display: 'flex', alignItems: 'center', gap: 1.5, py: 1.25, borderBottom: 1, borderColor: 'divider', cursor: 'pointer', '&:hover': { bgcolor: tokens.sageSoft } }}
                    >
                      <Chip size="small" label={r.version_label} sx={{ flexShrink: 0 }} />
                      <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                        <Typography variant="body2" noWrap title={r.session_name || 'Edited by hand'}>
                          {r.session_name || 'Edited by hand'}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {/* Meeting date, not pipeline run date — see revisionDate in ModuleDocument. */}
                          {[fmtDate(r.call_date || r.created_at), `${r.change_count} change${r.change_count === 1 ? '' : 's'}`].filter(Boolean).join(' · ')}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                  <Button size="small" startIcon={<History />} onClick={() => onHistory(row)} sx={{ mt: 1.5 }}>
                    {revisions.length > RECENT_REVISIONS ? `All ${revisions.length} revisions with changes` : 'View changes'}
                  </Button>
                </>
              )
        )}
      </Box>

      {/* Footer actions */}
      <Divider />
      <Box sx={{
        p: 2, pb: 'calc(16px + env(safe-area-inset-bottom))', display: 'grid', gap: 1, gridTemplateColumns: '1fr 1fr',
        '& .MuiButton-root': { whiteSpace: 'nowrap', minWidth: 0, px: 1.5 },
        '& .MuiButton-startIcon': { mr: 0.75 },
      }}>
        <Button
          variant="outlined"
          startIcon={busy ? <CircularProgress size={16} /> : <Download />}
          disabled={!row?.has_content || busy}
          onClick={() => onDownload(row)}
        >
          Download Word
        </Button>
        <Button variant="contained" startIcon={<OpenInNew />} onClick={() => onOpen(row)}>
          Open full page
        </Button>
      </Box>
    </Box>
  );
}

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Box, Paper, Typography, Button, Alert, CircularProgress, Divider, Snackbar,
  TextField, Tooltip, Tabs, Tab, Chip, List, ListItemButton, ListItemText,
} from '@mui/material';
import {
  ArrowBack, Download, Edit, Save, Close, Visibility, History, Article,
} from '@mui/icons-material';
import MarkdownRenderer from '../chat/MarkdownRenderer';
import { modulesAPI } from '../../services/api';
import { useAuth } from '../../auth/AuthContext';

/**
 * Structural markers, for the same guard the Minutes editor uses.
 *
 * Editing here is for correcting wording — a mis-transcribed term, a requirement phrased wrongly.
 * The bullet count matters more than it does for minutes: every bullet becomes a requirement row
 * and a vector, so removing one removes it from what the chatbot can answer.
 */
const structureOf = (markdown) => {
  const lines = (markdown || '').split('\n');
  return {
    headings: lines.filter((l) => /^#{1,6}\s/.test(l.trim())).length,
    bullets: lines.filter((l) => /^\s*[-*+]\s/.test(l)).length,
  };
};

/**
 * When this revision happened, as a reader means it.
 *
 * The meeting date, not the day the pipeline ran — those are the same thing for a normal upload
 * but months apart for a backfill, where every revision would otherwise carry the backfill date
 * and the history would read in the wrong order. A hand edit has no meeting, so it falls back to
 * when it was saved.
 */
const revisionDate = (revision) => {
  const when = revision?.call_date || revision?.created_at;
  return when ? new Date(when).toLocaleDateString() : '';
};

/** One line of the red/green view. */
function DiffLine({ line }) {
  if (line.type === 'same') {
    return (
      <Box component="pre" sx={diffLineSx('transparent', 'text.secondary')}>
        {line.text || ' '}
      </Box>
    );
  }
  const added = line.type === 'added';
  return (
    <Box
      component="pre"
      sx={diffLineSx(added ? '#e6ffed' : '#ffeef0', added ? '#116329' : '#82071e')}
    >
      {(added ? '+ ' : '- ') + (line.text || '')}
    </Box>
  );
}

const diffLineSx = (background, color) => ({
  m: 0,
  px: 1,
  py: '1px',
  background,
  color,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 12.5,
  lineHeight: 1.6,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
});

/**
 * One module's living document.
 *
 * Two views of the same thing: the document as it stands now, and how it got there. The history
 * is the part a Word file or a shared folder cannot give you — each revision names the meeting
 * that produced it and says which requirement changed, not merely that the file did.
 */
export default function ModuleDocument() {
  const { moduleId } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { isAdmin } = useAuth();

  const [module, setModule] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState(searchParams.get('tab') === 'history' ? 1 : 0);

  const [revisions, setRevisions] = useState([]);
  const [selected, setSelected] = useState(null);      // the loaded revision detail
  const [loadingRevision, setLoadingRevision] = useState(false);

  const [downloading, setDownloading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'error' });

  const notify = (message, severity = 'error') => setSnackbar({ open: true, message, severity });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const [doc, revs] = await Promise.all([
          modulesAPI.get(moduleId),
          modulesAPI.revisions(moduleId).catch(() => []),
        ]);
        if (cancelled) return;
        setModule(doc);
        setRevisions(revs || []);
      } catch (err) {
        if (!cancelled) setError(err.response?.data?.detail || 'Could not load this document.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [moduleId]);

  const openRevision = async (revision) => {
    setLoadingRevision(true);
    setSelected(null);
    try {
      setSelected(await modulesAPI.revision(moduleId, revision.id));
    } catch (err) {
      notify(err.response?.data?.detail || 'Could not load that version.');
    } finally {
      setLoadingRevision(false);
    }
  };

  const changeTab = (_e, value) => {
    setTab(value);
    // Keeps the URL shareable: /documents/:id?tab=history opens straight onto the history.
    setSearchParams(value === 1 ? { tab: 'history' } : {}, { replace: true });
  };

  const download = async (version) => {
    setDownloading(true);
    try {
      await modulesAPI.download(module.id, module.module_name, version);
    } catch (err) {
      notify(err.message || 'Download failed.');
    } finally {
      setDownloading(false);
    }
  };

  const startEdit = () => {
    setDraft(module.content_markdown || '');
    setPreview(false);
    setEditing(true);
  };

  const save = async () => {
    if (!draft.trim()) {
      notify('The document cannot be empty.');
      return;
    }

    const before = structureOf(module.content_markdown);
    const after = structureOf(draft);
    if (after.bullets < before.bullets || before.headings !== after.headings) {
      const parts = [];
      if (before.headings !== after.headings) {
        parts.push(`headings ${before.headings} → ${after.headings}`);
      }
      if (before.bullets !== after.bullets) {
        parts.push(`requirements ${before.bullets} → ${after.bullets}`);
      }
      const ok = window.confirm(
        `This edit changes the document's structure (${parts.join(', ')}).\n\n` +
        `Every bullet is a requirement the chatbot can answer from, so removing one removes it ` +
        `from search as well.\n\nSave anyway?`,
      );
      if (!ok) return;
    }

    setSaving(true);
    try {
      const updated = await modulesAPI.update(module.id, draft, 'Edited by hand.');
      setModule(updated);
      setEditing(false);
      setRevisions(await modulesAPI.revisions(moduleId).catch(() => revisions));
      notify(`Saved as ${updated.current_version} and re-indexed for chat.`, 'success');
    } catch (err) {
      notify(err.response?.data?.detail || err.message || 'Could not save.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 8 }}><CircularProgress /></Box>
    );
  }

  if (error || !module) {
    return (
      <Box sx={{ p: 3, maxWidth: 900, margin: '0 auto' }}>
        <Button startIcon={<ArrowBack />} onClick={() => navigate('/documents')} sx={{ mb: 2 }}>
          Back to documents
        </Button>
        <Alert severity="error">{error || 'Document not found.'}</Alert>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3, maxWidth: 1100, margin: '0 auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Button startIcon={<ArrowBack />} onClick={() => navigate('/documents')}>
          Back to documents
        </Button>
        <Box sx={{ display: 'flex', gap: 1 }}>
          {isAdmin && !editing && (
            <Tooltip title="Correct wording in this document">
              <Button size="small" startIcon={<Edit />} onClick={startEdit}>Edit</Button>
            </Tooltip>
          )}
          {editing && (
            <>
              <Button
                size="small"
                startIcon={preview ? <Edit /> : <Visibility />}
                onClick={() => setPreview((p) => !p)}
              >
                {preview ? 'Edit' : 'Preview'}
              </Button>
              <Button size="small" startIcon={<Close />} onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button
                size="small"
                variant="contained"
                startIcon={saving ? <CircularProgress size={14} /> : <Save />}
                disabled={saving}
                onClick={save}
              >
                Save
              </Button>
            </>
          )}
          {!editing && (
            <Button
              size="small"
              variant="outlined"
              startIcon={downloading ? <CircularProgress size={14} /> : <Download />}
              disabled={downloading}
              onClick={() => download(null)}
            >
              Word
            </Button>
          )}
        </Box>
      </Box>

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
        <Typography variant="h5" fontWeight="bold">{module.module_name}</Typography>
        <Chip size="small" label={module.current_version} />
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {module.requirement_count} requirements · {module.revision_count} revisions
        {module.last_synced_at
          ? ` · last updated ${new Date(module.last_synced_at).toLocaleDateString()}`
          : ''}
      </Typography>

      <Tabs value={tab} onChange={changeTab} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Tab icon={<Article fontSize="small" />} iconPosition="start" label="Document" />
        <Tab
          icon={<History fontSize="small" />}
          iconPosition="start"
          label={`History (${revisions.length})`}
        />
      </Tabs>

      {/* ── Document ───────────────────────────────────────────────────────── */}
      {tab === 0 && (
        <Paper elevation={2} sx={{ p: { xs: 2, md: 4 }, borderRadius: 2 }}>
          {editing ? (
            <>
              <Alert severity="info" sx={{ mb: 2 }}>
                Saving records a new version and immediately rebuilds what the chatbot answers
                from. Correct wording here; use a grooming call to change what was agreed.
              </Alert>
              {preview ? (
                <MarkdownRenderer content={draft} />
              ) : (
                <TextField
                  fullWidth
                  multiline
                  minRows={24}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck
                  sx={{ '& textarea': { fontFamily: 'ui-monospace, monospace', fontSize: 13.5 } }}
                />
              )}
            </>
          ) : (
            <MarkdownRenderer content={module.content_markdown || ''} />
          )}
        </Paper>
      )}

      {/* ── History ──────────────────────────────────────────────────────────
          Laid out with flex rather than MUI Grid: the Grid API changed in v7 and the old
          `item xs={}` props are silently ignored, which lays a page out wrong without
          failing the build. */}
      {tab === 1 && (
        <Box sx={{ display: 'flex', gap: 2, flexDirection: { xs: 'column', md: 'row' } }}>
          <Box sx={{ flex: '0 0 auto', width: { xs: '100%', md: 320 } }}>
            <Paper elevation={2} sx={{ borderRadius: 2 }}>
              <List dense disablePadding>
                {revisions.length === 0 && (
                  <Box sx={{ p: 3, color: 'text.secondary' }}>
                    <Typography variant="body2">No revisions yet.</Typography>
                  </Box>
                )}
                {revisions.map((r) => (
                  <ListItemButton
                    key={r.id}
                    selected={selected?.id === r.id}
                    onClick={() => openRevision(r)}
                    sx={{ borderBottom: '1px solid', borderColor: 'divider' }}
                  >
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <Chip size="small" label={r.version_label} />
                          <Typography variant="caption" color="text.secondary">
                            {r.change_count} change{r.change_count === 1 ? '' : 's'}
                          </Typography>
                        </Box>
                      }
                      secondary={
                        <>
                          {r.session_name || 'Edited by hand'}
                          {revisionDate(r) ? ` · ${revisionDate(r)}` : ''}
                        </>
                      }
                    />
                  </ListItemButton>
                ))}
              </List>
            </Paper>
          </Box>

          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Paper elevation={2} sx={{ p: 2, borderRadius: 2, minHeight: 240 }}>
              {loadingRevision && (
                <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
                  <CircularProgress />
                </Box>
              )}

              {!loadingRevision && !selected && (
                <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
                  Select a version to see what changed.
                </Typography>
              )}

              {!loadingRevision && selected && (
                <>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Typography variant="subtitle1" fontWeight="bold">
                      {selected.previous_version
                        ? `${selected.previous_version} → ${selected.version_label}`
                        : `${selected.version_label} — first version`}
                    </Typography>
                    <Button
                      size="small"
                      startIcon={<Download />}
                      onClick={() => download(selected.version_label)}
                    >
                      This version
                    </Button>
                  </Box>

                  <Typography variant="caption" color="text.secondary">
                    {selected.session_name || 'Edited by hand'}
                    {revisionDate(selected) ? ` · ${revisionDate(selected)}` : ''}
                  </Typography>

                  {selected.revision_summary && (
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                      {selected.revision_summary}
                    </Typography>
                  )}

                  <Divider sx={{ my: 1.5 }} />

                  {/* The reconciler's own account comes first: it says WHICH requirement changed
                      and what it said before, which the line diff below cannot express. */}
                  {selected.line_diffs?.length > 0 && (
                    <>
                      <Typography variant="overline" color="text.secondary">
                        Changes ({selected.line_diffs.length})
                      </Typography>
                      <Box sx={{ mb: 2 }}>
                        {selected.line_diffs.map((d, i) => (
                          <Box key={i} sx={{ mb: 1.2 }}>
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <Chip
                                size="small"
                                label={d.action}
                                color={
                                  d.action === 'MODIFIED' ? 'warning'
                                    : d.action === 'DEPRECATED' ? 'error' : 'success'
                                }
                                variant="outlined"
                              />
                              {d.sub_heading && (
                                <Typography variant="caption" color="text.secondary">
                                  {d.sub_heading}
                                </Typography>
                              )}
                            </Box>
                            {d.old_text && (
                              <Typography
                                variant="body2"
                                sx={{ color: '#82071e', textDecoration: 'line-through', ml: 1 }}
                              >
                                {d.old_text}
                              </Typography>
                            )}
                            {d.new_text && (
                              <Typography variant="body2" sx={{ color: '#116329', ml: 1 }}>
                                {d.new_text}
                              </Typography>
                            )}
                          </Box>
                        ))}
                      </Box>
                    </>
                  )}

                  <Typography variant="overline" color="text.secondary">
                    Document
                  </Typography>
                  <Box
                    sx={{
                      mt: 0.5,
                      border: '1px solid',
                      borderColor: 'divider',
                      borderRadius: 1,
                      maxHeight: 520,
                      overflow: 'auto',
                      background: '#fafbfc',
                    }}
                  >
                    {(selected.diff_lines || []).map((line, i) => (
                      <DiffLine key={i} line={line} />
                    ))}
                  </Box>
                </>
              )}
            </Paper>
          </Box>
        </Box>
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

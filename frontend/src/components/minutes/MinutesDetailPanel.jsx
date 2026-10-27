import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Avatar, AvatarGroup, Box, Button, CircularProgress, Divider, IconButton, Skeleton, Tab,
  Tabs, Tooltip, Typography,
} from '@mui/material';
import {
  Close, DescriptionOutlined, Download, Autorenew, Send, PlaylistAddCheck, TopicOutlined,
  TaskAltOutlined, GroupsOutlined,
} from '@mui/icons-material';
import MarkdownRenderer from '../chat/MarkdownRenderer';
import StatusChip from '../common/StatusChip';
import { momAPI } from '../../services/api';
import { tokens, categoryColors } from '../../theme/theme';
import { STATUS, failureReason, showTasksCreatedChip, tasksActionLabel } from './minutesStatus';
import { deriveTopics, countActionItems, deriveOwners, initials, formatCallDate } from './momParse';

const KEY_DISCUSSIONS = 5;
const MAX_AVATARS = 4;

const fmtDateTime = (v) => (v ? new Date(v).toLocaleString() : '—');

function StatTile({ icon, value, label }) {
  return (
    <Box sx={{ flex: 1, minWidth: 0, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 2, bgcolor: 'background.paper', display: 'flex', alignItems: 'center', gap: 1.25 }}>
      <Box sx={{ width: '2.25rem', height: '2.25rem', borderRadius: 1.5, display: 'grid', placeItems: 'center', bgcolor: tokens.sageLight, color: 'secondary.main', flexShrink: 0 }}>
        {icon}
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ fontSize: '1.25rem', fontWeight: 700, lineHeight: 1.1 }}>{value}</Typography>
        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>{label}</Typography>
      </Box>
    </Box>
  );
}

function DetailRow({ label, children }) {
  return (
    <Box sx={{ display: 'flex', gap: 2, py: 1, borderBottom: 1, borderColor: 'divider' }}>
      <Typography variant="body2" color="text.secondary" sx={{ width: '8.5rem', flexShrink: 0 }}>{label}</Typography>
      <Typography variant="body2" component="div" sx={{ minWidth: 0, wordBreak: 'break-word' }}>{children}</Typography>
    </Box>
  );
}

/**
 * Right-hand panel for one meeting's minutes (or a full-screen drawer on phones).
 *
 * Fetches the full MOM (with content_markdown) for `row.mom.id`, using `cache` (a Map owned by
 * MinutesPage) so flipping between cards doesn't refetch. MinutesPage deletes the cache entry
 * after a send/regenerate so the panel shows fresh details.
 *
 * Actions are handed back to MinutesPage — it owns the confirmations, snackbars and list reload.
 */
export default function MinutesDetailPanel({
  row, statusKey, projectName, isAdmin, busy, cache, onClose, onDownload, onRegenerate, onSend, onReviewTasks,
}) {
  const listMom = row?.mom;
  const momId = listMom?.id;
  const [tab, setTab] = useState(0);
  const [, rerender] = useState(0);
  const [errors, setErrors] = useState({}); // by mom id

  // The cache is the source of truth; this effect only fills it. `listMom` is a dependency so a
  // list reload (new row objects) refetches an entry MinutesPage invalidated after send/regenerate.
  //
  // Deliberately NO "cancelled" flag: if the effect re-runs while a fetch is in flight (list
  // reload, StrictMode), the re-run sees `cache.has` once that fetch lands and skips — so the
  // landing fetch itself must always trigger the re-render, or the panel sticks on skeletons.
  // Re-rendering an unmounted component is a no-op in React 18+, and results are keyed by id,
  // so a late response can never show under the wrong meeting.
  useEffect(() => {
    if (!momId || cache.has(momId)) return;
    momAPI.get(momId)
      .then((data) => { cache.set(momId, data); rerender((n) => n + 1); })
      .catch((err) => {
        setErrors((e) => ({ ...e, [momId]: err.response?.data?.detail || 'Could not load these minutes.' }));
      });
  }, [momId, listMom, cache]);

  const mom = (momId && cache.get(momId)) || null;
  const error = (momId && errors[momId]) || '';

  const md = mom?.content_markdown || '';
  // "Open Items" is a list of questions, not a discussion topic — keep it out of the count.
  const topics = useMemo(() => deriveTopics(md).filter((t) => !/^open items\b/i.test(t.title)), [md]);
  const actionCount = useMemo(() => countActionItems(md), [md]);
  const owners = useMemo(() => deriveOwners(md), [md]);
  const docTitle = useMemo(() => (/^#\s+(.+)$/m.exec(md)?.[1] || '').replace(/[*_`]/g, ''), [md]);

  const dateText = formatCallDate(row?.call_date);
  const readable = mom && !mom.generation_error && md;
  const loading = momId && !mom && !error;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, bgcolor: 'background.paper' }}>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5, px: 2.5, pt: 2.5, pb: 1.5 }}>
        <Box sx={{ width: '2.75rem', height: '2.75rem', borderRadius: 2, display: 'grid', placeItems: 'center', bgcolor: tokens.sageLight, color: 'primary.main', flexShrink: 0 }}>
          <DescriptionOutlined />
        </Box>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography variant="h4" component="h2" sx={{ fontSize: '1.2rem', wordBreak: 'break-word' }}>
              {row?.session_name}
            </Typography>
            {isAdmin && statusKey && (
              <Tooltip title={failureReason(row?.mom)}>
                <span><StatusChip status={STATUS[statusKey].label} /></span>
              </Tooltip>
            )}
            {isAdmin && row && showTasksCreatedChip(row) && (
              <Tooltip title={`Approved by ${row.mom.approved_by}; tasks created in the tracker.`}>
                <span><StatusChip status="approved" label="Tasks created" /></span>
              </Tooltip>
            )}
          </Box>
          <Typography variant="caption" color="text.secondary">
            {[dateText, projectName].filter(Boolean).join('  |  ')}
          </Typography>
        </Box>
        <IconButton onClick={onClose} aria-label="Close panel" size="small"><Close /></IconButton>
      </Box>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="fullWidth" sx={{ borderBottom: 1, borderColor: 'divider', minHeight: '2.75rem', '& .MuiTab-root': { minHeight: '2.75rem' } }}>
        <Tab label="Preview" />
        <Tab label="Document" />
        <Tab label="Details" />
      </Tabs>

      {/* Body */}
      <Box sx={{ flexGrow: 1, minHeight: 0, overflowY: 'auto', px: 2.5, py: 2 }}>
        {error && <Alert severity="error">{error}</Alert>}
        {loading && (
          <Box>
            <Skeleton variant="rounded" height={90} sx={{ mb: 2 }} />
            <Skeleton variant="rounded" height={64} sx={{ mb: 2 }} />
            <Skeleton variant="text" /><Skeleton variant="text" /><Skeleton variant="text" width="60%" />
          </Box>
        )}

        {mom && mom.truncated && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            These minutes reached the generation length limit and may be incomplete toward the end.
          </Alert>
        )}
        {mom && !readable && tab !== 2 && (
          <Alert severity="error">{mom.generation_error || 'These minutes have no content.'}</Alert>
        )}

        {/* Preview */}
        {readable && tab === 0 && (
          <>
            <Box sx={{ p: 2, borderRadius: 2, bgcolor: tokens.sageSoft, border: 1, borderColor: 'divider', mb: 2 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 1, alignItems: 'baseline' }}>
                <Typography variant="h4" component="h3" sx={{ fontSize: '1.05rem' }}>Minutes of Meeting</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>{dateText}</Typography>
              </Box>
              {docTitle && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{docTitle}</Typography>}
            </Box>

            <Box sx={{ display: 'flex', gap: 1, mb: 2.5 }}>
              <StatTile icon={<TopicOutlined fontSize="small" />} value={topics.length} label="Topics" />
              <StatTile icon={<TaskAltOutlined fontSize="small" />} value={actionCount} label="Action items" />
              <StatTile icon={<GroupsOutlined fontSize="small" />} value={owners.length} label="People" />
            </Box>

            {topics.length > 0 && (
              <Box sx={{ mb: 2.5 }}>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>Key Discussions</Typography>
                <Box component="ul" sx={{ m: 0, pl: 2.5, '& li': { mb: 0.75 } }}>
                  {topics.slice(0, KEY_DISCUSSIONS).map((t) => (
                    <Typography component="li" variant="body2" key={t.title}>{t.title}</Typography>
                  ))}
                </Box>
                {topics.length > KEY_DISCUSSIONS && (
                  <Button size="small" onClick={() => setTab(1)} sx={{ mt: 0.5, minHeight: 0 }}>
                    + {topics.length - KEY_DISCUSSIONS} more in the document
                  </Button>
                )}
              </Box>
            )}

            {owners.length > 0 && (
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  People <Typography component="span" variant="caption" color="text.secondary">(owners of action items and priorities)</Typography>
                </Typography>
                <AvatarGroup max={MAX_AVATARS + 1} sx={{ justifyContent: 'flex-end', flexDirection: 'row-reverse', '& .MuiAvatar-root': { width: '2.25rem', height: '2.25rem', fontSize: '0.8rem', fontWeight: 600 } }}>
                  {owners.map((name, i) => {
                    const c = categoryColors[i % categoryColors.length];
                    return (
                      <Tooltip key={name} title={name}>
                        <Avatar sx={{ bgcolor: c.bg, color: c.fg }}>{initials(name)}</Avatar>
                      </Tooltip>
                    );
                  })}
                </AvatarGroup>
              </Box>
            )}
          </>
        )}

        {/* Document */}
        {readable && tab === 1 && <MarkdownRenderer content={md} />}

        {/* Details */}
        {mom && tab === 2 && (
          <Box>
            <DetailRow label="Version">{mom.version ?? '—'}</DetailRow>
            <DetailRow label="Generated">{fmtDateTime(mom.created_at)}</DetailRow>
            <DetailRow label="Model">{mom.model_used || '—'}</DetailRow>
            <DetailRow label="Approved">
              {mom.approved_at ? `${fmtDateTime(mom.approved_at)} by ${mom.approved_by}` : 'Not yet'}
            </DetailRow>
            <DetailRow label="Emailed">{mom.email_sent_at ? fmtDateTime(mom.email_sent_at) : 'Not yet'}</DetailRow>
            <DetailRow label="Recipients">
              {mom.email_recipients?.length ? mom.email_recipients.join(', ') : '—'}
            </DetailRow>
            {mom.generation_error && <DetailRow label="Generation error">{mom.generation_error}</DetailRow>}
            {mom.email_error && <DetailRow label="Email error">{mom.email_error}</DetailRow>}
          </Box>
        )}
      </Box>

      {/* Footer actions */}
      <Divider />
      <Box sx={{
        p: 2, pb: 'calc(16px + env(safe-area-inset-bottom))', display: 'grid', gap: 1, gridTemplateColumns: '1fr 1fr',
        // One line per button so the grid rows stay equal height (a wrapped label made one taller).
        '& .MuiButton-root': { whiteSpace: 'nowrap', minWidth: 0, px: 1.5 },
        '& .MuiButton-startIcon': { mr: 0.75 },
      }}>
        <Button
          variant="outlined"
          startIcon={busy ? <CircularProgress size={16} /> : <Download />}
          disabled={!readable || busy}
          onClick={() => onDownload(mom)}
          sx={{ gridColumn: isAdmin && row?.transcript_id ? 'auto' : '1 / -1' }}
        >
          Download
        </Button>
        {isAdmin && row?.transcript_id && (
          <Button variant="outlined" startIcon={<Autorenew />} disabled={busy} onClick={() => onRegenerate(row.transcript_id)}>
            Regenerate
          </Button>
        )}
        {isAdmin && (
          <>
            <Button variant="outlined" startIcon={<Send />} disabled={!readable || busy} onClick={() => onSend(mom)}>
              Send
            </Button>
            {/* Navy while tasks still need creating (the pending action); plain like the others once done. */}
            <Button variant={mom?.approved_at ? 'outlined' : 'contained'} startIcon={<PlaylistAddCheck />}
                    disabled={!readable || busy} onClick={() => onReviewTasks(mom)}>
              {tasksActionLabel(mom)}
            </Button>
          </>
        )}
      </Box>
    </Box>
  );
}

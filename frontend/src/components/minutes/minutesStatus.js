/**
 * One display state per meeting row, used by the cards, the filter pills and StatusChip.
 *
 * The backend tracks two independent things: the email (`status`: draft | sent | send_failed)
 * and approval (`approved_at`: tasks pushed to the tracker). The UI shows ONE pill, so they are
 * ranked: a failure needs attention first, then how far the minutes have travelled.
 *
 * `row` is a MinutesPage row: { transcript_id, session_name, call_date, mom }.
 */
export const STATUS = {
  not_generated: { label: 'Not generated', chip: 'neutral' },
  failed: { label: 'Failed', chip: 'failed' },
  sent: { label: 'Sent', chip: 'sent' },
  approved: { label: 'Approved', chip: 'approved' },
  draft: { label: 'Draft', chip: 'draft' },
};

export const statusOf = (row) => {
  const mom = row.mom;
  if (!mom) return 'not_generated';
  if (mom.generation_error || mom.status === 'send_failed') return 'failed';
  if (mom.status === 'sent') return 'sent';
  if (mom.approved_at) return 'approved';
  return 'draft';
};

// Filter pills. "Approved" counts every meeting whose tasks were created, even once it has been
// sent (its one pill then says Sent) — otherwise approved-and-sent minutes vanish from that filter.
export const matchesFilter = (row, key) => {
  if (key === 'all') return true;
  if (key === 'approved') return !!row.mom?.approved_at;
  return statusOf(row) === key;
};

// Approval = the tasks were created in the tracker. Shown as a second chip when the main pill
// (e.g. Sent) would hide it.
export const showTasksCreatedChip = (row) => !!row.mom?.approved_at && statusOf(row) !== 'approved';

export const tasksActionLabel = (mom) => (mom?.approved_at ? 'View created tasks' : 'Create tasks');

// Why a row is "failed", for the chip tooltip.
export const failureReason = (mom) => {
  if (!mom) return '';
  if (mom.generation_error) return `Generation failed: ${mom.generation_error}`;
  if (mom.status === 'send_failed') return `Email failed${mom.email_error ? `: ${mom.email_error}` : ''}`;
  return '';
};

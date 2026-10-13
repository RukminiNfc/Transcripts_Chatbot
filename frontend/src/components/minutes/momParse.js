/**
 * Read-only helpers over a MOM's `content_markdown`.
 *
 * The MOM prompt (Backend/app/prompts/mom_generation_prompt.txt) fixes the document's shape:
 *
 *   # Meeting Requirements — {date}
 *   ## [Major Area]            ...one per topic
 *   ## Open Items / To Be Confirmed
 *   ## Consolidated Action Items
 *   ### [Area]
 *   - [Action] — owner: [name], due: [date]
 *   ## Team Priorities
 *   ### [Owner name]
 *
 * Everything here is lifted from that structure — nothing is summarised or invented. If a
 * document deviates (older prompt, truncated output) the helpers degrade to empty results.
 */

// Markdown emphasis/links stripped back to plain text, for one-line labels.
export const plain = (text) =>
  text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/[*_`]/g, '').trim();

/**
 * "What was discussed", derived from the document's own structure.
 *
 * The MOM prompt forbids summarizing ("omission is a defect"), so there is no overview paragraph
 * to preview — but its `##` sections ARE the meeting's topic list, one per feature/process area.
 * Each topic takes the first bullet beneath it as a one-line illustration. Nothing is invented:
 * every word here is lifted from the document.
 *
 * The two trailing sections are roll-ups of what came before, not topics in their own right.
 */
const APPENDIX_SECTIONS = /^(consolidated action items|team priorities)\b/i;

export const deriveTopics = (markdown) => {
  const topics = [];
  let current = null;
  // `^##\s` cannot match a `###` line — the third character is '#', not whitespace.
  for (const line of (markdown || '').split('\n')) {
    const heading = /^##\s+(.+?)\s*$/.exec(line);
    if (heading) {
      const title = plain(heading[1]);
      current = !title || APPENDIX_SECTIONS.test(title) ? null : { title, detail: '' };
      if (current) topics.push(current);
      continue;
    }
    if (!current || current.detail) continue;
    const bullet = /^\s*[-*]\s+(.+?)\s*$/.exec(line);
    if (bullet) current.detail = plain(bullet[1]);
  }
  return topics;
};

// Lines of one `## <title>` section (up to the next `## ` heading), or [] if absent.
const sectionLines = (markdown, titleRe) => {
  const out = [];
  let inside = false;
  for (const line of (markdown || '').split('\n')) {
    const heading = /^##\s+(.+?)\s*$/.exec(line);
    if (heading) {
      inside = titleRe.test(plain(heading[1]));
      continue;
    }
    if (inside) out.push(line);
  }
  return out;
};

const BULLET = /^\s*[-*]\s+\S/;

/** Number of action bullets under "## Consolidated Action Items". */
export const countActionItems = (markdown) =>
  sectionLines(markdown, /^consolidated action items\b/i).filter((l) => BULLET.test(l)).length;

// Placeholders the model writes when nobody owns an item — not people.
const NOT_A_PERSON = /^(tbd|tbc|n\/?a|none|unassigned|unknown|team|all|everyone|-+)$/i;

const cleanName = (raw) => plain(raw).replace(/^[\s:–—-]+|[\s.,;:–—-]+$/g, '');

/**
 * People who own something in this meeting: `owner: X` in the action items plus the
 * `### Name` headings under Team Priorities. Unique (case-insensitive), in first-seen order.
 * These are owners, not a full attendee list — the transcript does not record attendance.
 */
export const deriveOwners = (markdown) => {
  const seen = new Map();
  const add = (name) => {
    const n = cleanName(name);
    if (!n || NOT_A_PERSON.test(n) || n.length > 60) return;
    const key = n.toLowerCase();
    if (!seen.has(key)) seen.set(key, n);
  };

  for (const line of sectionLines(markdown, /^consolidated action items\b/i)) {
    const m = /owners?:\s*([^,;()\n]+)/i.exec(line);
    // "owner: Priya and Ravi" / "Priya / Ravi" / "Priya & Ravi" -> two people.
    if (m) m[1].split(/\s*(?:\/|&|\band\b)\s*/i).forEach(add);
  }
  for (const line of sectionLines(markdown, /^team priorities\b/i)) {
    const h = /^###\s+(.+?)\s*$/.exec(line);
    if (h) add(h[1]);
  }
  return [...seen.values()];
};

/** Two-letter initials for an avatar: "Priya Sharma" -> "PS", "Ravi" -> "RA". */
export const initials = (name) => {
  const parts = (name || '').split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return (parts[0] || '?').slice(0, 2).toUpperCase();
};

/**
 * A call date for display. A date-only string ('2026-05-04') parses as UTC midnight, which shows
 * as the PREVIOUS day in any behind-UTC timezone — so pin those to local midnight.
 */
export const formatCallDate = (raw, opts = { month: 'short', day: 'numeric', year: 'numeric' }) => {
  if (!raw) return '';
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00` : raw);
  return isNaN(d) ? '' : d.toLocaleDateString(undefined, opts);
};

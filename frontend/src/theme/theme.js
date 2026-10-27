import { createTheme, alpha } from '@mui/material/styles';

/**
 * The app's single source of colour, type and component styling.
 *
 * Pages should take colours from here (theme.palette / the `--mui-*` CSS variables that
 * `cssVariables: true` emits) instead of hardcoding hex in `sx`. The palette follows the
 * redesign mockups: navy for primary actions, sage for brand accents, a warm off-white surface.
 */

// Raw tokens — exported so a component that needs a value outside the MUI palette (e.g. a
// chip colour pair) still reads it from one place.
export const tokens = {
  navy: '#1F2A44',
  navyDark: '#141C30',
  sage: '#5E7A63',
  sageLight: '#E8EEE8',
  sageSoft: '#F1F4EF',
  surface: '#F7F6F2',
  card: '#FFFFFF',
  border: '#E4E2DC',
  borderLight: '#EFEDE8',
  text: '#1F2937',
  textMuted: '#6B7280',
  textFaint: '#9CA3AF',
  // Panel widths in rem so they shrink with LAPTOP_SCALE below (14.5rem = 232px at 100%).
  sidebarWidth: { md: '14.5rem', xl: '16.5rem' },
  chatListWidth: { md: '17rem', xl: '20.625rem' },
};

/**
 * Laptop density. Screens 900–1535px wide (most laptops, especially with Windows display scaling)
 * render the whole UI at 80% — the same result as browser zoom 80%, but without CSS `zoom`,
 * which mis-positions MUI menus/popovers. It works because:
 *   - all font sizes and panel widths are in rem  -> scaled by the root font-size
 *   - all MUI spacing is calc(n * var(--mui-spacing)) -> scaled by the spacing variable
 * Phones and large monitors (1536px+) stay at 100%. Use rem (not px) for new sizes.
 */
const LAPTOP_SCALE = 0.8;
const LAPTOP_MEDIA = '@media (min-width:900px) and (max-width:1535.95px)';

// Status chip pairs (bg / text). Kept here so StatusChip and any future badge agree.
export const statusColors = {
  draft: { bg: '#FDF1E3', fg: '#9A5B13' },
  approved: { bg: '#E3F1E6', fg: '#2F6B3C' },
  sent: { bg: '#E3ECFA', fg: '#2C5AA0' },
  failed: { bg: '#FBE4E4', fg: '#B42318' },
  neutral: { bg: '#EFEDE8', fg: '#4B5563' },
};

// Category chip pairs — assigned deterministically per category name (see CategoryChip).
export const categoryColors = [
  { bg: '#E3ECFA', fg: '#2C5AA0' },
  { bg: '#E3F1E6', fg: '#2F6B3C' },
  { bg: '#EEE7FA', fg: '#5B3FA0' },
  { bg: '#E0F2F1', fg: '#1F6F6A' },
  { bg: '#FDF1E3', fg: '#9A5B13' },
  { bg: '#FBE7EF', fg: '#9D2F5B' },
];

const serif = '"Source Serif 4", Georgia, "Times New Roman", serif';
const sans = '"Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

const theme = createTheme({
  cssVariables: true,
  palette: {
    primary: { main: tokens.navy, dark: tokens.navyDark, contrastText: '#FFFFFF' },
    secondary: { main: tokens.sage, light: tokens.sageLight, contrastText: '#FFFFFF' },
    background: { default: tokens.surface, paper: tokens.card },
    text: { primary: tokens.text, secondary: tokens.textMuted, disabled: tokens.textFaint },
    divider: tokens.border,
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: sans,
    fontSize: 13,
    // Serif only for page-level headings; body/chat text stays sans for long-form readability.
    h1: { fontFamily: serif, fontWeight: 600, fontSize: '2rem' },
    h2: { fontFamily: serif, fontWeight: 600, fontSize: '1.75rem' },
    h3: { fontFamily: serif, fontWeight: 600, fontSize: '1.25rem' },
    h4: { fontFamily: serif, fontWeight: 600, fontSize: '1.125rem' },
    h5: { fontWeight: 600, fontSize: '1.125rem' },
    h6: { fontWeight: 600, fontSize: '1rem' },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: tokens.surface },
        [LAPTOP_MEDIA]: {
          // :root:root outranks MUI's own :root rule that declares --mui-spacing.
          ':root:root': { '--mui-spacing': `${8 * LAPTOP_SCALE}px` },
          html: { fontSize: `${100 * LAPTOP_SCALE}%` },
        },
        // Visible keyboard focus everywhere (mouse clicks don't trigger :focus-visible).
        ':focus-visible': { outline: `2px solid ${tokens.sage}`, outlineOffset: 2 },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { borderRadius: 8, minHeight: '2.5rem' },
        outlined: { borderColor: tokens.border, color: tokens.text, backgroundColor: tokens.card },
      },
    },
    MuiIconButton: {
      // Comfortable minimum tap target for touch.
      styleOverrides: { root: { minWidth: '2.5rem', minHeight: '2.5rem' } },
    },
    MuiChip: {
      styleOverrides: { root: { fontWeight: 500, borderRadius: 999 } },
    },
    MuiCard: {
      defaultProps: { elevation: 0 },
      styleOverrides: { root: { border: `1px solid ${tokens.border}`, borderRadius: 12 } },
    },
    MuiPaper: {
      styleOverrides: { outlined: { borderColor: tokens.border } },
    },
    MuiTableCell: {
      styleOverrides: {
        head: {
          fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
          color: tokens.textMuted, backgroundColor: tokens.sageSoft,
        },
        root: { borderColor: tokens.borderLight },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: { backgroundColor: tokens.card, '& .MuiOutlinedInput-notchedOutline': { borderColor: tokens.border } },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          '&.Mui-selected': { backgroundColor: alpha(tokens.sage, 0.14) },
          '&.Mui-selected:hover': { backgroundColor: alpha(tokens.sage, 0.2) },
        },
      },
    },
    MuiDrawer: {
      styleOverrides: { paper: { backgroundColor: tokens.surface, borderRight: `1px solid ${tokens.border}` } },
    },
    MuiTab: {
      styleOverrides: { root: { textTransform: 'none', fontWeight: 600 } },
    },
  },
});

export default theme;

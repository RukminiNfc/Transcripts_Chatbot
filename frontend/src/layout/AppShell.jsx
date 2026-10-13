import React, { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  AppBar, Avatar, Box, Button, Divider, Drawer, IconButton, List, ListItemButton, ListItemIcon,
  ListItemText, ListSubheader, Menu, MenuItem, Select, Toolbar, Typography, useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import {
  Add, ChatBubbleOutline, DescriptionOutlined, FactCheckOutlined, SettingsOutlined,
  FolderOpen, Logout, Menu as MenuIcon, ArticleOutlined, ChevronRight,
} from '@mui/icons-material';
import { useAuth } from '../auth/AuthContext';
import { useProject } from '../projects/ProjectContext';
import { momAPI } from '../services/api';
import { tokens } from '../theme/theme';

/**
 * App chrome: a permanent left sidebar on desktop (md+), a top bar + slide-in drawer on mobile.
 *
 * To add a page (Documents, Projects, Integrations, Settings…), add one entry to NAV_ITEMS.
 * `match` decides which item is highlighted for the current path.
 */
const NAV_ITEMS = [
  { label: 'Chat', to: '/', icon: <ChatBubbleOutline />, match: (p) => p === '/' },
  { label: 'Minutes of Meeting', to: '/minutes', icon: <DescriptionOutlined />, match: (p) => p.startsWith('/minutes') },
  { label: 'Requirements', to: '/requirements', icon: <FactCheckOutlined />, match: (p) => p.startsWith('/requirements'), adminOnly: true },
  { label: 'Admin', to: '/admin', icon: <SettingsOutlined />, match: (p) => p.startsWith('/admin'), adminOnly: true },
];

const RECENT_MEETINGS = 5;

function ProjectSelector() {
  const { projects, projectId, setProjectId } = useProject();
  if (projects.length === 0) return null;
  return (
    <Box sx={{ px: 2, pb: { md: 1.5, xl: 2 } }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        Project
      </Typography>
      <Select
        size="small"
        fullWidth
        value={projectId || ''}
        onChange={(e) => setProjectId(e.target.value)}
        startAdornment={<FolderOpen fontSize="small" sx={{ mr: 1, color: 'text.secondary' }} />}
        inputProps={{ 'aria-label': 'Project' }}
      >
        {projects.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
      </Select>
    </Box>
  );
}

// Latest minutes for the selected project. Non-critical: on error the section just hides.
function RecentMeetings({ onNavigate }) {
  const { projectId } = useProject();
  const location = useLocation();
  const [items, setItems] = useState([]);

  useEffect(() => {
    let cancelled = false;
    momAPI.list(projectId)
      .then((list) => {
        if (cancelled) return;
        const sorted = [...(list || [])]
          .sort((a, b) => String(b.call_date || '').localeCompare(String(a.call_date || '')))
          .slice(0, RECENT_MEETINGS);
        setItems(sorted);
      })
      .catch(() => { if (!cancelled) setItems([]); });
    return () => { cancelled = true; };
  }, [projectId]);

  if (items.length === 0) return null;
  return (
    <List
      dense
      subheader={(
        <ListSubheader disableSticky sx={{ bgcolor: 'transparent', lineHeight: '32px', fontWeight: 500 }}>
          Recent Meetings
        </ListSubheader>
      )}
      sx={{ px: 1 }}
    >
      {items.map((m) => (
        <ListItemButton
          key={m.id}
          component={Link}
          to={`/minutes/${m.id}`}
          onClick={onNavigate}
          selected={location.pathname === `/minutes/${m.id}`}
          sx={{ py: 0.5 }}
        >
          <ListItemIcon sx={{ minWidth: '1.875rem' }}><ArticleOutlined fontSize="small" /></ListItemIcon>
          <ListItemText primary={m.session_name} slotProps={{ primary: { noWrap: true, fontSize: '0.7812rem' } }} />
        </ListItemButton>
      ))}
    </List>
  );
}

function UserChip() {
  const { user, logout } = useAuth();
  const [anchor, setAnchor] = useState(null);
  const initial = (user?.username || '?').charAt(0).toUpperCase();
  return (
    <>
      <ListItemButton
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{ mx: 1, mb: 1, mt: 'auto', flexGrow: 0, py: 0.75, borderTop: 1, borderColor: 'divider', borderRadius: 0 }}
        aria-label="Account menu"
      >
        <Avatar sx={{ width: '2rem', height: '2rem', bgcolor: 'primary.main', fontSize: '0.875rem', mr: 1.5 }}>{initial}</Avatar>
        <ListItemText
          primary={user?.username}
          secondary={user?.role}
          slotProps={{ primary: { noWrap: true, fontSize: '0.8125rem', fontWeight: 500 }, secondary: { fontSize: '0.7188rem' } }}
        />
        <ChevronRight fontSize="small" sx={{ color: 'text.secondary' }} />
      </ListItemButton>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      >
        <MenuItem onClick={() => { setAnchor(null); logout(); }}>
          <ListItemIcon><Logout fontSize="small" /></ListItemIcon>
          Log out
        </MenuItem>
      </Menu>
    </>
  );
}

function SidebarContent({ onNavigate }) {
  const { isAdmin } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const items = NAV_ITEMS.filter((i) => !i.adminOnly || isAdmin);

  // "New" starts a fresh chat; ChatInterface watches for this navigation state.
  const handleNew = () => {
    navigate('/', { state: { newChat: Date.now() } });
    onNavigate?.();
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', flexGrow: 1, minHeight: '100%' }}>
      <Box sx={{ px: 2.5, pt: { md: 2, xl: 2.5 }, pb: { md: 1.5, xl: 2 } }}>
        <Box component="img" src="/nfclogo.jpg" alt="NFC" sx={{ height: { md: '2.25rem', xl: '2.75rem' }, borderRadius: 1, display: 'block' }} />
        <Typography variant="caption" sx={{ letterSpacing: '0.12em', color: 'text.secondary', textTransform: 'uppercase' }}>
          Transcript Chatbot
        </Typography>
      </Box>

      {/* Project first: it scopes everything below it. */}
      <ProjectSelector />

      <Box sx={{ px: 2, pb: 2 }}>
        <Button fullWidth variant="contained" startIcon={<Add />} onClick={handleNew} sx={{ justifyContent: 'flex-start', py: { md: 0.75, xl: 1.1 } }}>
          New Chat
        </Button>
      </Box>

      <List sx={{ px: 1 }}>
        {items.map((item) => (
          <ListItemButton
            key={item.to}
            component={Link}
            to={item.to}
            onClick={onNavigate}
            selected={item.match(location.pathname)}
            sx={{ mb: 0.25, py: { md: 0.75, xl: 1.1 } }}
          >
            <ListItemIcon sx={{ minWidth: '2.25rem', color: 'text.primary' }}>{item.icon}</ListItemIcon>
            <ListItemText
              primary={item.label}
              slotProps={{ primary: { noWrap: true, fontWeight: 500, sx: { fontSize: { xs: '0.8125rem', xl: '0.875rem' } } } }}
            />
          </ListItemButton>
        ))}
      </List>

      <Divider sx={{ mx: 2, my: 1 }} />

      <Box sx={{ flexGrow: 1, overflowY: 'auto' }}>
        <RecentMeetings onNavigate={onNavigate} />
      </Box>

      <UserChip />
    </Box>
  );
}

// Title shown in the mobile top bar.
const titleFor = (path) => NAV_ITEMS.find((i) => i.match(path))?.label || 'Transcript Chatbot';

export default function AppShell({ children }) {
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const { user } = useAuth();

  const closeMobile = () => setMobileOpen(false);

  return (
    <Box sx={{ display: 'flex', height: '100dvh', bgcolor: 'background.default' }}>
      {isDesktop ? (
        <Drawer
          variant="permanent"
          // Paper height matches the main column (100dvh) so the user chip sits at the window's bottom edge.
          sx={{ width: tokens.sidebarWidth, flexShrink: 0, '& .MuiDrawer-paper': { width: tokens.sidebarWidth, height: '100dvh', boxSizing: 'border-box' } }}
        >
          <SidebarContent />
        </Drawer>
      ) : (
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={closeMobile}
          ModalProps={{ keepMounted: true }}
          sx={{ '& .MuiDrawer-paper': { width: 'min(300px, 85vw)', boxSizing: 'border-box' } }}
        >
          <SidebarContent onNavigate={closeMobile} />
        </Drawer>
      )}

      <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {!isDesktop && (
          <AppBar
            position="static"
            color="inherit"
            elevation={0}
            sx={{ borderBottom: 1, borderColor: 'divider', bgcolor: 'background.paper', pt: 'env(safe-area-inset-top)' }}
          >
            <Toolbar sx={{ gap: 1 }}>
              <IconButton edge="start" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
                <MenuIcon />
              </IconButton>
              <Typography variant="h6" component="div" noWrap sx={{ flexGrow: 1 }}>
                {titleFor(location.pathname)}
              </Typography>
              <Avatar sx={{ width: '2rem', height: '2rem', bgcolor: 'primary.main', fontSize: '0.875rem' }}>
                {(user?.username || '?').charAt(0).toUpperCase()}
              </Avatar>
            </Toolbar>
          </AppBar>
        )}
        <Box component="main" sx={{ flexGrow: 1, minHeight: 0, overflowY: 'auto' }}>
          {children}
        </Box>
      </Box>
    </Box>
  );
}

import React from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Box, List, ListItemButton, ListItemIcon, ListItemText, Tab, Tabs, Typography, useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { FolderOutlined, GroupsOutlined } from '@mui/icons-material';
import TeamSubscriptions from './TeamSubscriptions';
import { useProject } from '../../projects/ProjectContext';
import { tokens } from '../../theme/theme';

/**
 * Project Configuration (admin): the one-off setup that used to live in the Admin page.
 *
 * Laid out like Chat: a full-height middle column listing the sections (same width/background as
 * the conversation list), and the selected section's content on the right. Sections are nested
 * routes (/settings/projects, /settings/team) so each is deep-linkable. On phones the sections
 * become a tab row above the content.
 *
 * To add a section (Users & logins, Integrations…), add an entry here and a route in App.jsx.
 */
const SETTINGS_SECTIONS = [
  { path: 'projects', label: 'Projects', icon: <FolderOutlined /> },
  { path: 'team', label: 'Team & Notifications', icon: <GroupsOutlined /> },
];

export default function ProjectConfigurationPage() {
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));
  const location = useLocation();
  const navigate = useNavigate();
  const current = SETTINGS_SECTIONS.find((s) => location.pathname.startsWith(`/settings/${s.path}`))?.path
    || SETTINGS_SECTIONS[0].path;

  if (!isDesktop) {
    return (
      <Box sx={{ px: 2, py: 2 }}>
        <Typography variant="h3" component="h1" sx={{ mb: 1 }}>Project Configuration</Typography>
        <Tabs
          value={current}
          onChange={(_, v) => navigate(`/settings/${v}`)}
          variant="scrollable"
          allowScrollButtonsMobile
          sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}
        >
          {SETTINGS_SECTIONS.map((s) => (
            <Tab key={s.path} value={s.path} label={s.label} icon={s.icon} iconPosition="start" sx={{ minHeight: '3rem' }} />
          ))}
        </Tabs>
        <Outlet />
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', height: '100%', bgcolor: 'background.default' }}>
      {/* Middle column — mirrors Chat's conversation list */}
      <Box sx={{
        width: tokens.chatListWidth, flexShrink: 0, borderRight: 1, borderColor: 'divider',
        display: 'flex', flexDirection: 'column', minHeight: 0,
      }}>
        <Box sx={{ px: 2.5, pt: { xs: 2, xl: 3 }, pb: 1.5 }}>
          <Typography variant="h3" component="h1">Project Configuration</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Set up projects and the people who receive their minutes and tasks.
          </Typography>
        </Box>
        <List sx={{ px: 1, overflowY: 'auto' }}>
          {SETTINGS_SECTIONS.map((s) => (
            <ListItemButton
              key={s.path}
              component={NavLink}
              to={`/settings/${s.path}`}
              selected={current === s.path}
              sx={{ mb: 0.25, py: 1 }}
            >
              <ListItemIcon sx={{ minWidth: '2.25rem', color: 'text.secondary' }}>{s.icon}</ListItemIcon>
              <ListItemText primary={s.label} slotProps={{ primary: { fontWeight: 500 } }} />
            </ListItemButton>
          ))}
        </List>
      </Box>

      {/* Section content */}
      <Box sx={{ flexGrow: 1, minWidth: 0, overflowY: 'auto', bgcolor: tokens.card, px: 4, py: { xs: 2, xl: 3 } }}>
        <Outlet />
      </Box>
    </Box>
  );
}

// /settings/team — the team of the project selected in the sidebar.
export function TeamSettings() {
  const { projectId, project } = useProject();
  return <TeamSubscriptions projectId={projectId} projectName={project?.name} />;
}

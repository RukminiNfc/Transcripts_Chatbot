import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom';
import { AppBar, Toolbar, Typography, Button, Box, Select, MenuItem } from '@mui/material';
import { Chat, AdminPanelSettings, Logout, Description, FolderOpen } from '@mui/icons-material';
import ChatInterface from './components/chat/ChatInterface';
import AdminDashboard from './components/admin/AdminDashboard';
import MinutesPage from './components/minutes/MinutesPage';
import MinutesDocument from './components/minutes/MinutesDocument';
import MinutesTasksReview from './components/minutes/MinutesTasksReview';
import RequirementsDashboard from './components/requirements/RequirementsDashboard';
import Login from './auth/Login';
import ProtectedRoute from './auth/ProtectedRoute';
import { useAuth } from './auth/AuthContext';
import { ProjectProvider, useProject } from './projects/ProjectContext';

// Scopes Admin → Transcripts and the Minutes page. Chat is not scoped yet.
function ProjectSelector() {
  const { projects, projectId, setProjectId } = useProject();
  if (projects.length === 0) return null;
  return (
    <Select
      size="small"
      value={projectId || ''}
      onChange={(e) => setProjectId(e.target.value)}
      startAdornment={<FolderOpen fontSize="small" sx={{ mr: 1, opacity: 0.8 }} />}
      sx={{
        ml: 2, minWidth: 200, color: 'inherit', bgcolor: 'rgba(255,255,255,0.12)',
        '& .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,0.4)' },
        '& .MuiSvgIcon-root': { color: 'inherit' },
      }}
      inputProps={{ 'aria-label': 'Project' }}
    >
      {projects.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
    </Select>
  );
}

function NavBar() {
  const { isAuthenticated, isAdmin, user, logout } = useAuth();
  const location = useLocation();

  // No navbar on the login page or when logged out.
  if (!isAuthenticated || location.pathname === '/login') return null;

  return (
    <AppBar position="static">
      <Toolbar>
        <Box sx={{ flexGrow: 1, display: 'flex', alignItems: 'center' }}>
          <img src="/nfclogo.jpg" alt="NFC Logo" style={{ height: 40, borderRadius: 4 }} />
          <ProjectSelector />
        </Box>

        <Button color="inherit" component={Link} to="/" startIcon={<Chat />}>
          Chat
        </Button>

        {/* Minutes is open to every logged-in user — reading and downloading need no admin role. */}
        <Button color="inherit" component={Link} to="/minutes" startIcon={<Description />}>
          Minutes
        </Button>

        {/* Admin-only menus */}
        {isAdmin && (
          <>
            <Button color="inherit" component={Link} to="/admin" startIcon={<AdminPanelSettings />}>
              Admin
            </Button>
            <Button color="inherit" component={Link} to="/requirements">
              Requirements
            </Button>
          </>
        )}

        <Typography variant="body2" sx={{ mx: 2, opacity: 0.9 }}>
          {user?.username} ({user?.role})
        </Typography>
        <Button color="inherit" onClick={logout} startIcon={<Logout />}>
          Logout
        </Button>
      </Toolbar>
    </AppBar>
  );
}

function App() {
  return (
    <Router>
      <ProjectProvider>
      <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
        <NavBar />
        <Box sx={{ flexGrow: 1 }}>
          <Routes>
            <Route path="/login" element={<Login />} />

            {/* Any logged-in user */}
            <Route path="/" element={<ProtectedRoute><ChatInterface /></ProtectedRoute>} />
            <Route path="/minutes" element={<ProtectedRoute><MinutesPage /></ProtectedRoute>} />
            <Route path="/minutes/:momId" element={<ProtectedRoute><MinutesDocument /></ProtectedRoute>} />
            <Route path="/minutes/:momId/tasks" element={<ProtectedRoute requireAdmin><MinutesTasksReview /></ProtectedRoute>} />

            {/* Admin only */}
            <Route path="/admin" element={<ProtectedRoute requireAdmin><AdminDashboard /></ProtectedRoute>} />
            <Route path="/requirements" element={<ProtectedRoute requireAdmin><RequirementsDashboard /></ProtectedRoute>} />

            {/* Anything else -> Chat (or login if not authenticated) */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Box>
      </Box>
      </ProjectProvider>
    </Router>
  );
}

export default App;

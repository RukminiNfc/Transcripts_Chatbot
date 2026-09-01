import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom';
import { AppBar, Toolbar, Typography, Button, Box } from '@mui/material';
import { Chat, AdminPanelSettings, Logout } from '@mui/icons-material';
import ChatInterface from './components/chat/ChatInterface';
import AdminDashboard from './components/admin/AdminDashboard';
import RequirementsDashboard from './components/requirements/RequirementsDashboard';
import Login from './auth/Login';
import ProtectedRoute from './auth/ProtectedRoute';
import { useAuth } from './auth/AuthContext';

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
        </Box>

        <Button color="inherit" component={Link} to="/" startIcon={<Chat />}>
          Chat
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
      <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
        <NavBar />
        <Box sx={{ flexGrow: 1 }}>
          <Routes>
            <Route path="/login" element={<Login />} />

            {/* Any logged-in user */}
            <Route path="/" element={<ProtectedRoute><ChatInterface /></ProtectedRoute>} />

            {/* Admin only */}
            <Route path="/admin" element={<ProtectedRoute requireAdmin><AdminDashboard /></ProtectedRoute>} />
            <Route path="/requirements" element={<ProtectedRoute requireAdmin><RequirementsDashboard /></ProtectedRoute>} />

            {/* Anything else -> Chat (or login if not authenticated) */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Box>
      </Box>
    </Router>
  );
}

export default App;

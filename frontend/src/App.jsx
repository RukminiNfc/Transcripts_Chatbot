import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import { AppBar, Toolbar, Typography, Button, Container, Box } from '@mui/material';
import { Chat, AdminPanelSettings } from '@mui/icons-material';
import ChatInterface from './components/chat/ChatInterface';
import AdminDashboard from './components/admin/AdminDashboard';
import RequirementsDashboard from './components/requirements/RequirementsDashboard';

function App() {
  return (
    <Router>
      <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
        <AppBar position="static">
          <Toolbar>
            <Box sx={{ flexGrow: 1, display: 'flex', alignItems: 'center' }}>
              <img src="/nfclogo.jpg" alt="NFC Logo" style={{ height: '40px', borderRadius: '4px' }} />
            </Box>

            <Button color="inherit" component={Link} to="/" startIcon={<Chat />}>
              Chat
            </Button>

            <Button color="inherit" component={Link} to="/admin" startIcon={<AdminPanelSettings />}>
              Admin
            </Button>
            
            <Button color="inherit" component={Link} to="/requirements">
              Requirements
            </Button>
          </Toolbar>
        </AppBar>

        <Box sx={{ flexGrow: 1 }}>
          <Routes>
            <Route path="/" element={<ChatInterface />} />
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/requirements" element={<RequirementsDashboard />} />
          </Routes>
        </Box>
      </Box>
    </Router>
  );
}

export default App;
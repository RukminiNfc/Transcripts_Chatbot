import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, Outlet } from 'react-router-dom';
import ChatInterface from './components/chat/ChatInterface';
import TranscriptsPage from './components/transcripts/TranscriptsPage';
import ProjectConfigurationPage, { TeamSettings } from './components/settings/ProjectConfigurationPage';
import ProjectsSettings from './components/settings/ProjectsSettings';
import MinutesPage from './components/minutes/MinutesPage';
import MinutesDocument from './components/minutes/MinutesDocument';
import MinutesTasksReview from './components/minutes/MinutesTasksReview';
import RequirementsDashboard from './components/requirements/RequirementsDashboard';
import Login from './auth/Login';
import ProtectedRoute from './auth/ProtectedRoute';
import AppShell from './layout/AppShell';
import { ProjectProvider } from './projects/ProjectContext';

// Every signed-in page renders inside the sidebar shell; Login renders bare.
// The selected project scopes Transcripts, Minutes, Requirements and Project Configuration → Team.
// Chat is not scoped yet.
function ShellLayout() {
  return (
    <ProtectedRoute>
      <AppShell>
        <Outlet />
      </AppShell>
    </ProtectedRoute>
  );
}

function App() {
  return (
    <Router>
      <ProjectProvider>
        <Routes>
          <Route path="/login" element={<Login />} />

          <Route element={<ShellLayout />}>
            {/* Any logged-in user */}
            <Route path="/" element={<ChatInterface />} />
            <Route path="/minutes" element={<MinutesPage />} />
            <Route path="/minutes/:momId" element={<MinutesDocument />} />
            <Route path="/minutes/:momId/tasks" element={<ProtectedRoute requireAdmin><MinutesTasksReview /></ProtectedRoute>} />

            {/* Admin only */}
            <Route path="/transcripts" element={<ProtectedRoute requireAdmin><TranscriptsPage /></ProtectedRoute>} />
            <Route path="/requirements" element={<ProtectedRoute requireAdmin><RequirementsDashboard /></ProtectedRoute>} />
            {/* Project Configuration — sections are nested routes so each one is deep-linkable. */}
            <Route path="/settings" element={<ProtectedRoute requireAdmin><ProjectConfigurationPage /></ProtectedRoute>}>
              <Route index element={<Navigate to="projects" replace />} />
              <Route path="projects" element={<ProjectsSettings />} />
              <Route path="team" element={<TeamSettings />} />
            </Route>
            {/* The old Admin page was split into Transcripts + Project Configuration. */}
            <Route path="/admin/*" element={<Navigate to="/settings" replace />} />
          </Route>

          {/* Anything else -> Chat (or login if not authenticated) */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ProjectProvider>
    </Router>
  );
}

export default App;

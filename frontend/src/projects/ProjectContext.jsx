import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { projectsAPI } from '../services/api';
import { useAuth } from '../auth/AuthContext';

/**
 * The selected project, shared by every page.
 *
 * Picked in the nav bar (or with "Use this Project" in Admin) and remembered in localStorage, so a
 * refresh or page change keeps it. Admin → Transcripts and the Minutes page show only this
 * project's data. Chat is not scoped yet.
 *
 * If the remembered project no longer exists (deleted), the first project is used instead.
 */
const STORAGE_KEY = 'activeProjectId';
const ProjectContext = createContext(null);

const readStored = () => {
  try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }
};
const writeStored = (id) => {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id); else localStorage.removeItem(STORAGE_KEY);
  } catch { /* storage unavailable — selection just won't survive a refresh */ }
};

export function ProjectProvider({ children }) {
  const { isAuthenticated } = useAuth();
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectIdState] = useState(readStored);
  const [loaded, setLoaded] = useState(false);

  const setProjectId = useCallback((id) => {
    setProjectIdState(id);
    writeStored(id);
  }, []);

  // Re-fetch the list (Admin calls this after creating or deleting a project).
  const reload = useCallback(async () => {
    if (!isAuthenticated) return;
    try {
      const list = await projectsAPI.list();
      setProjects(list);
      setProjectIdState((current) => {
        const next = list.some((p) => p.id === current) ? current : (list[0]?.id || null);
        writeStored(next);
        return next;
      });
    } catch {
      setProjects([]);
    } finally {
      setLoaded(true);
    }
  }, [isAuthenticated]);

  useEffect(() => { reload(); }, [reload]);

  const project = projects.find((p) => p.id === projectId) || null;

  return (
    <ProjectContext.Provider value={{ projects, projectId, project, setProjectId, reload, loaded }}>
      {children}
    </ProjectContext.Provider>
  );
}

export function useProject() {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error('useProject must be used within ProjectProvider');
  return ctx;
}

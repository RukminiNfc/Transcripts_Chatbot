import React, { createContext, useContext, useState } from 'react';
import { authAPI, setAuth, clearAuth } from '../services/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  // Initialise from localStorage so a refresh keeps you logged in.
  const [user, setUser] = useState(() => {
    const token = localStorage.getItem('token');
    if (!token) return null;
    return {
      token,
      role: localStorage.getItem('role'),
      username: localStorage.getItem('username'),
    };
  });

  const login = async (username, password) => {
    const data = await authAPI.login(username, password); // {access_token, role, username}
    setAuth(data);
    setUser({ token: data.access_token, role: data.role, username: data.username });
    return data;
  };

  const logout = () => {
    clearAuth();
    setUser(null);
  };

  const value = {
    user,
    isAuthenticated: !!user,
    isAdmin: user?.role === 'admin',
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

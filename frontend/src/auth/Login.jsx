import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Paper, TextField, Button, Typography, Alert } from '@mui/material';
import { useAuth } from './AuthContext';

export default function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(username, password);
      navigate('/'); // go to Chat after a successful login
    } catch (err) {
      setError(err?.response?.data?.detail || 'Invalid username or password');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', bgcolor: '#f5f7fb' }}>
      <Paper elevation={4} sx={{ p: 5, width: 460, maxWidth: '90%' }}>
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <img src="/nfclogo.jpg" alt="NFC" style={{ height: 72, borderRadius: 4 }} />
        </Box>
        <Typography variant="h4" align="center" gutterBottom sx={{ fontWeight: 600 }}>Sign in</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <form onSubmit={handleSubmit}>
          <TextField
            label="Username" fullWidth margin="normal" autoFocus required
            value={username} onChange={(e) => setUsername(e.target.value)}
          />
          <TextField
            label="Password" type="password" fullWidth margin="normal" required
            value={password} onChange={(e) => setPassword(e.target.value)}
          />
          <Button type="submit" variant="contained" fullWidth size="large"
                  sx={{ mt: 3, py: 1.3, fontSize: '1rem' }} disabled={loading}>
            {loading ? 'Signing in…' : 'Sign In'}
          </Button>
        </form>
      </Paper>
    </Box>
  );
}

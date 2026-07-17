import React, { useState, useEffect } from 'react';
import { 
  Box, Typography, Button, Paper, Table, TableBody, TableCell, 
  TableContainer, TableHead, TableRow, Chip, CircularProgress,
  FormControl, InputLabel, Select, MenuItem
} from '@mui/material';
import { FileDownload } from '@mui/icons-material';

const API_URL = 'http://localhost:8001';

export default function RequirementsDashboard() {
  const [requirements, setRequirements] = useState([]);
  const [loading, setLoading] = useState(false);
  const [customers, setCustomers] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState('');

  useEffect(() => {
    fetchCustomers();
  }, []);

  useEffect(() => {
    if (selectedCustomer) {
      fetchRequirements(selectedCustomer);
    } else {
      setRequirements([]);
    }
  }, [selectedCustomer]);

  const fetchCustomers = async () => {
    try {
      const res = await fetch(`${API_URL}/requirements/customers`);
      const data = await res.json();
      setCustomers(data);
      if (data.length > 0) {
        setSelectedCustomer(data[0].id);
      }
    } catch (e) {
      console.error('Failed to fetch customers', e);
    }
  };

  const fetchRequirements = async (customerId) => {
    try {
      setLoading(true);
      const response = await fetch(`${API_URL}/requirements/customer/${customerId}`);
      if (response.ok) {
        const data = await response.json();
        setRequirements(data);
      }
    } catch (error) {
      console.error('Failed to fetch requirements:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    if (!selectedCustomer) return;
    try {
      const response = await fetch(`${API_URL}/requirements/customer/${selectedCustomer}/export`);
      const data = await response.json();
      
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Requirements_Export_${new Date().toISOString().split('T')[0]}.json`;
      a.click();
    } catch (error) {
      console.error('Failed to export:', error);
    }
  };

  return (
    <Box sx={{ p: 3, maxWidth: 1200, margin: '0 auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <Typography variant="h4" fontWeight="bold">
            Requirements Dashboard
          </Typography>
          
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel>Project</InputLabel>
            <Select
              value={selectedCustomer}
              label="Project"
              onChange={(e) => setSelectedCustomer(e.target.value)}
            >
              {customers.map((c) => (
                <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>
        
        <Box sx={{ display: 'flex', gap: 2 }}>
          <Button 
            variant="outlined" 
            color="secondary" 
            startIcon={<FileDownload />}
            onClick={handleExport}
            disabled={requirements.length === 0 || !selectedCustomer}
          >
            Export Document
          </Button>
        </Box>
      </Box>

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
          <CircularProgress />
        </Box>
      ) : (
        <TableContainer component={Paper} elevation={3}>
          <Table>
            <TableHead sx={{ backgroundColor: '#f5f5f5' }}>
              <TableRow>
                <TableCell><b>Category</b></TableCell>
                <TableCell><b>Sub-Category</b></TableCell>
                <TableCell><b>Requirement Statement</b></TableCell>
                <TableCell><b>Transcript Date</b></TableCell>
                <TableCell><b>Last Updated</b></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {requirements.length > 0 ? (
                requirements.map((req) => (
                  <TableRow key={req.id} hover>
                    <TableCell><Chip label={req.category || 'General'} size="small" color="primary" variant="outlined"/></TableCell>
                    <TableCell>{req.sub_category || '-'}</TableCell>
                    <TableCell>{req.current_text}</TableCell>
                    <TableCell>{req.transcript_date ? new Date(req.transcript_date).toLocaleDateString() : 'N/A'}</TableCell>
                    <TableCell>{new Date(req.updated_at).toLocaleDateString()}</TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={5} align="center" sx={{ py: 5 }}>
                    <Typography color="textSecondary">
                      No active requirements found. Go to Admin to upload a transcript.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}

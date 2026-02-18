import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  CircularProgress,
  Alert,
} from '@mui/material';
import DocumentCard from './DocumentCard';
import { documentAPI, metadataAPI } from '../../services/api';

function DocumentList({ refreshTrigger }) {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState({
    jurisdiction: '',
    guide_type: '',
    status: '',
  });

  // Predefined guide types
  const guideTypes = [
    'Country Guide',
    'Trademark Cancellations Guide',
    'Trademark Enforcement Guide',
    'Trade Dress Guide',
    'Trademark Opposition Guide',
    'Madrid System Guide',
    'Guide to GIs and Certification & Collective Marks'
  ];

  // Dynamic jurisdictions from database
  const [jurisdictions, setJurisdictions] = useState([]);

  // Fetch jurisdictions on mount
  useEffect(() => {
    const fetchJurisdictions = async () => {
      try {
        const data = await metadataAPI.getJurisdictions();
        setJurisdictions(data);
      } catch (err) {
        console.error('Error fetching jurisdictions:', err);
      }
    };
    fetchJurisdictions();
  }, [refreshTrigger]);

  const loadDocuments = async () => {
    setLoading(true);
    setError(null);

    try {
      const filterParams = Object.keys(filters).reduce((acc, key) => {
        if (filters[key]) acc[key] = filters[key];
        return acc;
      }, {});

      const data = await documentAPI.list(filterParams);
      setDocuments(data);
    } catch (err) {
      console.error('Error loading documents:', err);
      setError('Failed to load documents');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDocuments();
  }, [filters, refreshTrigger]);

  const handleDelete = async (documentId) => {
    if (!window.confirm('Are you sure you want to delete this document?')) {
      return;
    }

    try {
      await documentAPI.delete(documentId);
      loadDocuments();
    } catch (err) {
      console.error('Error deleting document:', err);
      setError('Failed to delete document');
    }
  };

  return (
    <Paper elevation={3} sx={{ p: 3 }}>
      <Typography variant="h6" gutterBottom>
        Document Library
      </Typography>

      <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel>Jurisdiction</InputLabel>
          <Select
            value={filters.jurisdiction}
            label="Jurisdiction"
            onChange={(e) => setFilters({ ...filters, jurisdiction: e.target.value })}
          >
            <MenuItem value="">All</MenuItem>
            {jurisdictions.map((j) => (
              <MenuItem key={j} value={j}>{j}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel>Guide Type</InputLabel>
          <Select
            value={filters.guide_type}
            label="Guide Type"
            onChange={(e) => setFilters({ ...filters, guide_type: e.target.value })}
          >
            <MenuItem value="">All</MenuItem>
            {guideTypes.map((type) => (
              <MenuItem key={type} value={type}>{type}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel>Status</InputLabel>
          <Select
            value={filters.status}
            label="Status"
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
          >
            <MenuItem value="">All</MenuItem>
            <MenuItem value="published">Published</MenuItem>
            <MenuItem value="processing">Processing</MenuItem>
            <MenuItem value="failed">Failed</MenuItem>
          </Select>
        </FormControl>
      </Box>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress />
        </Box>
      ) : documents.length === 0 ? (
        <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
          <Typography>No documents found</Typography>
        </Box>
      ) : (
        <Box>
          {documents.map((doc) => (
            <DocumentCard key={doc.id} document={doc} onDelete={handleDelete} />
          ))}
        </Box>
      )}
    </Paper>
  );
}

export default DocumentList;
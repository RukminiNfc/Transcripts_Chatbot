import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  Button,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Alert,
  LinearProgress,
} from '@mui/material';
import { CloudUpload } from '@mui/icons-material';
import { documentAPI, metadataAPI } from '../../services/api';

function DocumentUpload({ onUploadSuccess }) {
  const [file, setFile] = useState(null);
  const [guideType, setGuideType] = useState('');
  const [jurisdiction, setJurisdiction] = useState('');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  // Predefined guide types (all 7 types)
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

  // Fetch existing jurisdictions on mount
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
  }, []);

  const handleFileChange = (e) => {
    const selectedFile = e.target.files[0];
    if (selectedFile && selectedFile.type === 'application/pdf') {
      setFile(selectedFile);
      setError(null);
    } else {
      setError('Please select a PDF file');
      setFile(null);
    }
  };

  const handleUpload = async () => {
    if (!file) {
      setError('Please select a file');
      return;
    }

    setUploading(true);
    setError(null);
    setSuccess(false);

    try {
      const result = await documentAPI.upload(file, guideType, jurisdiction);
      setSuccess(true);
      setFile(null);
      setGuideType('');
      setJurisdiction('');

      if (onUploadSuccess) {
        onUploadSuccess(result);
      }

      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      console.error('Upload error:', err);
      setError(err.response?.data?.detail || 'Upload failed. Please try again.');
    } finally {
      setUploading(false);
    }
  };

  return (
    <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
      <Typography variant="h6" gutterBottom>
        Upload New Document
      </Typography>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 2 }}>
          Document uploaded and processed successfully!
        </Alert>
      )}

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Button
          variant="outlined"
          component="label"
          startIcon={<CloudUpload />}
          disabled={uploading}
        >
          {file ? file.name : 'Select PDF File'}
          <input
            type="file"
            accept="application/pdf"
            hidden
            onChange={handleFileChange}
          />
        </Button>

        <FormControl fullWidth>
          <InputLabel>Guide Type</InputLabel>
          <Select
            value={guideType}
            label="Guide Type"
            onChange={(e) => setGuideType(e.target.value)}
            disabled={uploading}
          >
            <MenuItem value="">Select Guide Type</MenuItem>
            {guideTypes.map((type) => (
              <MenuItem key={type} value={type}>{type}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl fullWidth>
          <InputLabel>Jurisdiction</InputLabel>
          <Select
            value={jurisdiction}
            label="Jurisdiction"
            onChange={(e) => setJurisdiction(e.target.value)}
            disabled={uploading}
          >
            <MenuItem value="">Select or Type Jurisdiction</MenuItem>
            {jurisdictions.map((j) => (
              <MenuItem key={j} value={j}>{j}</MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* Allow custom jurisdiction input */}
        <TextField
          label="Or Enter New Jurisdiction"
          value={jurisdiction}
          onChange={(e) => setJurisdiction(e.target.value)}
          disabled={uploading}
          placeholder="e.g., Australia, Germany, Japan"
          size="small"
        />

        <Button
          variant="contained"
          onClick={handleUpload}
          disabled={!file || uploading}
          fullWidth
        >
          {uploading ? 'Processing...' : 'Upload and Process'}
        </Button>

        {uploading && <LinearProgress />}
      </Box>
    </Paper>
  );
}

export default DocumentUpload;
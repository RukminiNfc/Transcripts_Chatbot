import React, { useState } from 'react';
import { Container, Grid, Typography, Box } from '@mui/material';
import DocumentUpload from './DocumentUpload';
import DocumentList from './DocumentList';

function AdminDashboard() {
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  const handleUploadSuccess = () => {
    setRefreshTrigger((prev) => prev + 1);
  };

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Box sx={{ mb: 4 }}>
        <Typography variant="h4" gutterBottom sx={{ color: '#ff6900', fontWeight: 600 }}>
          Admin Dashboard
        </Typography>
        <Typography variant="body1" color="text.secondary">
          Upload and manage documents for the INTA RAG system
        </Typography>
      </Box>

      <Grid container spacing={3}>
        <Grid item xs={12} md={4}>
          <DocumentUpload onUploadSuccess={handleUploadSuccess} />
        </Grid>

        <Grid item xs={12} md={8}>
          <DocumentList refreshTrigger={refreshTrigger} />
        </Grid>
      </Grid>
    </Container>
  );
}

export default AdminDashboard;
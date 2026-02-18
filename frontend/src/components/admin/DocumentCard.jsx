import React from 'react';
import {
  Card,
  CardContent,
  Typography,
  Chip,
  Box,
  IconButton,
  Tooltip,
} from '@mui/material';
import { Delete, Description, CheckCircle, Error, HourglassEmpty } from '@mui/icons-material';

function DocumentCard({ document, onDelete }) {
  const getStatusIcon = (status) => {
    switch (status) {
      case 'published':
        return <CheckCircle color="success" />;
      case 'processing':
        return <HourglassEmpty color="warning" />;
      case 'failed':
        return <Error color="error" />;
      default:
        return <HourglassEmpty color="disabled" />;
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'published':
        return 'success';
      case 'processing':
        return 'warning';
      case 'failed':
        return 'error';
      default:
        return 'default';
    }
  };

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <Box sx={{ flexGrow: 1 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
              <Description sx={{ mr: 1, color: '#ff6900' }} />
              <Typography variant="h6">{document.title || document.filename}</Typography>
            </Box>

            <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
              <Chip
                icon={getStatusIcon(document.status)}
                label={document.status.toUpperCase()}
                size="small"
                color={getStatusColor(document.status)}
              />

              {document.jurisdiction && (
                <Chip
                  label={`🌍 ${document.jurisdiction}`}
                  size="small"
                  sx={{
                    borderColor: '#ff6900',
                    color: '#ff6900'
                  }}
                  variant="outlined"
                />
              )}

              {document.guide_type && (
                <Chip
                  label={document.guide_type}
                  size="small"
                  sx={{
                    borderColor: '#ff6900',
                    color: '#ff6900'
                  }}
                  variant="outlined"
                />
              )}
            </Box>

            <Typography variant="body2" color="text.secondary">
              {document.total_pages && `${document.total_pages} pages`}
              {document.total_chunks && ` • ${document.total_chunks} chunks`}
            </Typography>

            <Typography variant="caption" color="text.secondary">
              Uploaded: {new Date(document.upload_date).toLocaleString()}
            </Typography>
          </Box>

          <Tooltip title="Delete">
            <IconButton onClick={() => onDelete(document.id)} color="error">
              <Delete />
            </IconButton>
          </Tooltip>
        </Box>
      </CardContent>
    </Card>
  );
}

export default DocumentCard;
import React, { useState, useEffect } from 'react';
import {
    Box, Chip, CircularProgress, Typography, Fade, Paper
} from '@mui/material';
import { metadataAPI } from '../../services/api';
import './ChatStyles.css';

/**
 * GuideSuggestions - Shows dynamic hierarchy of guide types and countries.
 * Fetches data from /api/metadata/hierarchy.
 * Design: Two-row interaction. Row 1 = Guide Types. Row 2 = Countries for selected guide.
 */
function GuideSuggestions({ onGuideClick }) {
    const [hierarchy, setHierarchy] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedGuide, setSelectedGuide] = useState(null);
    const [showMore, setShowMore] = useState(false);

    useEffect(() => {
        const fetchHierarchy = async () => {
            try {
                const data = await metadataAPI.getHierarchy();
                setHierarchy(data || []);
            } catch (err) {
                console.error("Failed to fetch guide hierarchy", err);
            } finally {
                setLoading(false);
            }
        };
        fetchHierarchy();
    }, []);

    const handleGuideSelect = (guideType) => {
        if (selectedGuide === guideType) {
            setSelectedGuide(null); // Toggle off
        } else {
            setSelectedGuide(guideType);
            setShowMore(false); // Reset show more when switching guides
        }
    };

    const handleShowMore = () => {
        setShowMore(!showMore);
    };

    if (loading) return <Box sx={{ p: 2, display: 'flex', justifyContent: 'center' }}><CircularProgress size={24} /></Box>;
    if (!hierarchy.length) return null;

    // Find countries for the selected guide
    const activeGuideData = hierarchy.find(h => h.guide_type === selectedGuide);
    const activeCountries = activeGuideData ? activeGuideData.countries : [];

    return (
        <Box className="guide-suggestions" sx={{ width: '100%', mt: 2 }}>
            <Typography variant="subtitle2" color="text.primary" sx={{ mb: 1.5, fontWeight: 'bold' }}>
                Explore different guide types:
            </Typography>

            {/* Guide Type Chips Row */}
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
                {hierarchy.map((item) => (
                    <Chip
                        key={item.guide_type}
                        label={`${item.guide_type} (${item.countries.length})`}
                        onClick={() => handleGuideSelect(item.guide_type)}
                        color={selectedGuide === item.guide_type ? "primary" : "default"}
                        variant={selectedGuide === item.guide_type ? "filled" : "outlined"}
                        sx={{
                            cursor: 'pointer',
                            fontWeight: selectedGuide === item.guide_type ? 600 : 400,
                            borderWidth: '1px',
                            transition: 'all 0.2s',
                            '&:hover': {
                                backgroundColor: selectedGuide === item.guide_type ? '#e65100' : '#fff3e0',
                                borderColor: '#ff6900'
                            },
                            // If not selected, subtle styling
                            ...(selectedGuide !== item.guide_type && {
                                backgroundColor: 'transparent',
                                borderColor: '#e0e0e0',
                            }),
                            // If selected, brand color
                            ...(selectedGuide === item.guide_type && {
                                backgroundColor: '#ff6900',
                                color: 'white',
                                '&:hover': { backgroundColor: '#e65100' }
                            })
                        }}
                    />
                ))}
            </Box>

            {/* Countries Panel (Conditional) */}
            {selectedGuide && (
                <Fade in={Boolean(selectedGuide)}>
                    <Paper
                        elevation={0}
                        sx={{
                            p: 2,
                            backgroundColor: '#fffcf7',
                            border: '1px solid #ffe0b2',
                            borderRadius: 2
                        }}
                    >
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5, textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 'bold' }}>
                            Select a Country for {selectedGuide}:
                        </Typography>

                        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                            {activeCountries.slice(0, showMore ? undefined : 10).map((country) => (
                                <Chip
                                    key={country}
                                    label={country}
                                    onClick={() => onGuideClick({ type: 'country', value: country })}
                                    size="small"
                                    sx={{
                                        cursor: 'pointer',
                                        backgroundColor: 'white',
                                        border: '1px solid #e0e0e0',
                                        '&:hover': {
                                            backgroundColor: '#ff6900',
                                            borderColor: '#ff6900',
                                            color: '#ffffff'
                                        }
                                    }}
                                />
                            ))}

                            {activeCountries.length > 10 && (
                                <Chip
                                    label={showMore ? "Show Less" : `+${activeCountries.length - 10} More`}
                                    onClick={handleShowMore}
                                    size="small"
                                    sx={{
                                        fontWeight: 'bold',
                                        border: '1px dashed #ff6900',
                                        color: '#ff6900',
                                        backgroundColor: 'transparent',
                                        cursor: 'pointer'
                                    }}
                                />
                            )}
                        </Box>
                    </Paper>
                </Fade>
            )}
        </Box>
    );
}

export default GuideSuggestions;

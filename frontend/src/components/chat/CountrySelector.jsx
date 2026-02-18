import React, { useState, useEffect } from 'react';
import { metadataAPI } from '../../services/api';
import './ChatStyles.css';

/**
 * CountrySelector - Modal popup for selecting a country from available jurisdictions.
 * Features a searchable list with all jurisdictions from the database.
 */
function CountrySelector({ open, onSelect, onClose }) {
    const [jurisdictions, setJurisdictions] = useState([]);
    const [searchTerm, setSearchTerm] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (open) {
            setLoading(true);
            setSearchTerm('');
            metadataAPI.getJurisdictions()
                .then((data) => {
                    setJurisdictions(data);
                    setLoading(false);
                })
                .catch((err) => {
                    console.error('Error fetching jurisdictions:', err);
                    setLoading(false);
                });
        }
    }, [open]);

    if (!open) return null;

    const filtered = jurisdictions.filter((j) =>
        j.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const handleOverlayClick = (e) => {
        if (e.target === e.currentTarget) {
            onClose();
        }
    };

    return (
        <div className="country-selector-overlay" onClick={handleOverlayClick}>
            <div className="country-selector">
                <div className="country-selector__header">
                    <h3 className="country-selector__title">
                        🌍 Select a Country
                    </h3>
                    <input
                        className="country-selector__search"
                        type="text"
                        placeholder="Search countries..."
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        autoFocus
                    />
                </div>

                <div className="country-selector__list">
                    {loading ? (
                        <div style={{ padding: '20px', textAlign: 'center', color: '#999' }}>
                            Loading countries...
                        </div>
                    ) : filtered.length === 0 ? (
                        <div style={{ padding: '20px', textAlign: 'center', color: '#999' }}>
                            No countries found
                        </div>
                    ) : (
                        filtered.map((jurisdiction) => (
                            <div
                                key={jurisdiction}
                                className="country-selector__item"
                                onClick={() => onSelect(jurisdiction)}
                            >
                                <span className="country-selector__item-flag">🏳️</span>
                                <span>{jurisdiction}</span>
                            </div>
                        ))
                    )}
                </div>

                <div className="country-selector__footer">
                    <button className="country-selector__cancel" onClick={onClose}>
                        Cancel
                    </button>
                </div>
            </div>
        </div>
    );
}

export default CountrySelector;

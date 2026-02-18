import React from 'react';
import './ChatStyles.css';

/**
 * CountryContextBar - Shows the active country context with change/reset options.
 * Appears when a country is locked (either inferred or manually selected).
 */
function CountryContextBar({ country, onChangeCountry, onReset }) {
    if (!country) return null;

    return (
        <div className="country-context-bar">
            <span className="country-context-bar__flag">🏳️</span>
            <span className="country-context-bar__label">Showing results for</span>
            <span className="country-context-bar__country">{country}</span>
            <div className="country-context-bar__actions">
                <button
                    className="country-context-bar__btn"
                    onClick={onChangeCountry}
                    title="Change to a different country"
                >
                    Change Country
                </button>
                <button
                    className="country-context-bar__btn country-context-bar__btn--reset"
                    onClick={onReset}
                    title="Reset to global context"
                >
                    Reset to Global
                </button>
            </div>
        </div>
    );
}

export default CountryContextBar;

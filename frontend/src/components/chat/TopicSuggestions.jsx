import React from 'react';
import './ChatStyles.css';

/**
 * TopicSuggestions - Renders clickable topic chips below assistant messages.
 * When clicked, the topic text is populated into the MessageInput (editable before sending).
 */
function TopicSuggestions({ suggestions, onTopicClick }) {
    if (!suggestions || suggestions.length === 0) return null;

    return (
        <div className="topic-suggestions">
            <div className="topic-suggestions__label">
                💡 Related topics you can explore:
            </div>
            {suggestions.map((topicItem, index) => {
                const label = typeof topicItem === 'string' ? topicItem : topicItem.label;
                const question = typeof topicItem === 'string' ? topicItem : topicItem.question;

                return (
                    <button
                        key={index}
                        className="topic-chip"
                        onClick={() => onTopicClick(question)}
                        title={`Ask: ${question}`}
                    >
                        {label}
                    </button>
                );
            })}
        </div>
    );
}

export default TopicSuggestions;

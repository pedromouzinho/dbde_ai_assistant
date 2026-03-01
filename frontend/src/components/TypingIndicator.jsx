import React from 'react';
import { MILLENNIUM_LOGO_DATA_URI } from '../utils/constants.js';

export default function TypingIndicator({ text }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 16, animation: 'fadeUp 0.3s ease' }}>
      <img
        src={MILLENNIUM_LOGO_DATA_URI}
        alt="Millennium"
        style={{ width: 32, height: 32, borderRadius: 10, flexShrink: 0 }}
      />
      <div
        style={{
          background: 'white',
          borderRadius: '4px 16px 16px 16px',
          padding: '12px 18px',
          boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
          fontSize: 13,
          color: '#888',
        }}
      >
        {text || 'A pensar'}
        <span className="streaming-dot" />
        <span className="streaming-dot" style={{ animationDelay: '0.2s' }} />
        <span className="streaming-dot" style={{ animationDelay: '0.4s' }} />
      </div>
    </div>
  );
}

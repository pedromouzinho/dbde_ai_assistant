import React, { useState } from 'react';

export default function FeedbackWidget({ conversationId, messageIndex, onSubmit }) {
  const [submitted, setSubmitted] = useState(false);
  const [showNote, setShowNote] = useState(false);
  const [note, setNote] = useState('');
  const [selectedRating, setSelectedRating] = useState(0);

  function handleRatingClick(n) {
    setSelectedRating(n);
    if (n <= 4) {
      setShowNote(true);
      return;
    }
    setSubmitted(true);
    onSubmit(conversationId, messageIndex, n, '');
  }

  function submitWithNote() {
    setSubmitted(true);
    onSubmit(conversationId, messageIndex, selectedRating, note);
  }

  if (submitted) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10, fontSize: 12, color: '#888' }}>
        {'✓ Obrigado pelo feedback! '}
        <span style={{ color: '#DE3163', fontWeight: 600 }}>{`${selectedRating}/10`}</span>
      </div>
    );
  }

  if (showNote) {
    return (
      <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontSize: 12, color: '#DE3163', fontWeight: 600 }}>
          {`Rating: ${selectedRating}/10 — O que correu mal?`}
        </div>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Ex: Respondeu sobre o Jorge errado, não pediu clarificação..."
          onKeyDown={(e) => {
            if (e.key === 'Enter') submitWithNote();
          }}
          style={{
            border: '1.5px solid #E0E0E0',
            borderRadius: 10,
            padding: '8px 12px',
            fontSize: 12,
            width: '100%',
            maxWidth: 400,
            fontFamily: "'Montserrat', sans-serif",
          }}
        />
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={submitWithNote}
            style={{
              background: '#DE3163',
              color: 'white',
              border: 'none',
              borderRadius: 8,
              padding: '6px 16px',
              fontSize: 12,
              cursor: 'pointer',
              fontWeight: 600,
              fontFamily: "'Montserrat', sans-serif",
            }}
          >
            Enviar
          </button>
          <button
            onClick={() => {
              setSubmitted(true);
              onSubmit(conversationId, messageIndex, selectedRating, '');
            }}
            style={{
              background: 'none',
              color: '#999',
              border: '1px solid #ddd',
              borderRadius: 8,
              padding: '6px 12px',
              fontSize: 12,
              cursor: 'pointer',
              fontFamily: "'Montserrat', sans-serif",
            }}
          >
            Saltar
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 12, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 12, color: '#999', marginRight: 4 }}>Avaliar:</span>
      {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
        <button
          key={n}
          onClick={() => handleRatingClick(n)}
          style={{
            width: 26,
            height: 26,
            borderRadius: '50%',
            border: '1.5px solid #ddd',
            background: 'white',
            color: '#bbb',
            fontSize: 11,
            fontWeight: 600,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 0,
            transition: 'all 0.15s ease',
            fontFamily: "'Montserrat', sans-serif",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = n <= 4 ? '#FFE0E0' : n <= 6 ? '#FFF3E0' : '#E8F5E9';
            e.currentTarget.style.borderColor = n <= 4 ? '#DE3163' : n <= 6 ? '#FF9800' : '#4CAF50';
            e.currentTarget.style.color = n <= 4 ? '#DE3163' : n <= 6 ? '#FF9800' : '#4CAF50';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'white';
            e.currentTarget.style.borderColor = '#ddd';
            e.currentTarget.style.color = '#bbb';
          }}
        >
          {n}
        </button>
      ))}
    </div>
  );
}

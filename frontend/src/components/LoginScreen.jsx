import React, { useState } from 'react';
import { API_URL, MILLENNIUM_LOGO_DATA_URI } from '../utils/constants.js';

export default function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit() {
    if (!username.trim() || !password) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(API_URL + '/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Erro');
      }
      const data = await res.json();
      onLogin({
        username: data.username,
        role: data.role,
        display_name: data.display_name,
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
        background: '#EAE4DC',
      }}
    >
      <div
        style={{
          width: 400,
          padding: '48px 44px',
          background: 'white',
          borderRadius: 24,
          boxShadow: '0 20px 60px rgba(0,0,0,0.08), 0 1px 3px rgba(0,0,0,0.04)',
          border: '1px solid rgba(0,0,0,0.04)',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <img
            src={MILLENNIUM_LOGO_DATA_URI}
            alt="Millennium"
            style={{ width: 52, height: 52, borderRadius: 14, marginBottom: 20 }}
          />
          <div style={{ fontSize: 22, fontWeight: 700, color: '#1a1a1a', letterSpacing: '-0.3px' }}>Assistente AI DBDE</div>
          <div style={{ fontSize: 12, color: '#aaa', marginTop: 6, fontWeight: 500, letterSpacing: '0.5px', textTransform: 'uppercase' }}>
            Millennium BCP · v7.3.0
          </div>
        </div>

        {error ? (
          <div
            style={{
              background: 'rgba(222,49,99,0.06)',
              color: '#B0103A',
              padding: '10px 16px',
              borderRadius: 10,
              fontSize: 12,
              marginBottom: 16,
              textAlign: 'center',
              fontWeight: 500,
              border: '1px solid rgba(222,49,99,0.12)',
            }}
          >
            {error}
          </div>
        ) : null}

        <input
          className="login-input"
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
          style={{ marginBottom: 14 }}
        />

        <input
          className="login-input"
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
          style={{ marginBottom: 24 }}
        />

        <button
          className="login-btn"
          onClick={handleSubmit}
          disabled={loading || !username.trim() || !password}
        >
          {loading ? 'A autenticar...' : 'Entrar'}
        </button>
      </div>
    </div>
  );
}

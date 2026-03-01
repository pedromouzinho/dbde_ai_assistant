import React from 'react';
import ReactDOM from 'react-dom/client';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import App from './App.jsx';
import './styles/index.css';

window.addEventListener('error', function(event) {
  try {
    fetch('/api/client-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        error_type: 'uncaught_error',
        message: String(event.message || '').slice(0, 500),
        stack: String(event.error?.stack || '').slice(0, 2000),
        component: '',
        url: String(window.location.href).slice(0, 500),
        user_agent: String(navigator.userAgent).slice(0, 200),
      }),
    }).catch(() => {});
  } catch (_) {}
});

window.addEventListener('unhandledrejection', function(event) {
  try {
    fetch('/api/client-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        error_type: 'unhandled_rejection',
        message: String(event.reason?.message || event.reason || '').slice(0, 500),
        stack: String(event.reason?.stack || '').slice(0, 2000),
        component: '',
        url: String(window.location.href).slice(0, 500),
        user_agent: String(navigator.userAgent).slice(0, 200),
      }),
    }).catch(() => {});
  } catch (_) {}
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <ErrorBoundary name="App">
    <App />
  </ErrorBoundary>
);

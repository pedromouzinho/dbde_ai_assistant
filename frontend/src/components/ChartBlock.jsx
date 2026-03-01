import React, { useEffect, useRef } from 'react';
import { renderPlotlyChart } from '../utils/chart.js';

export default function ChartBlock({ chartSpec, chartId }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !chartSpec) return undefined;

    let cancelled = false;
    const timer = setTimeout(() => {
      if (!cancelled) renderPlotlyChart(chartId, chartSpec);
    }, 100);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      if (window.Plotly) {
        const el = document.getElementById(chartId);
        if (el) window.Plotly.purge(el);
      }
    };
  }, [chartSpec, chartId]);

  if (!chartSpec) return null;

  return (
    <div style={{ margin: '12px 0', background: 'white', borderRadius: 12, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
      {chartSpec?.layout?.title?.text ? (
        <div style={{ fontSize: 13, fontWeight: 600, color: '#333', marginBottom: 8 }}>
          {chartSpec.layout.title.text}
        </div>
      ) : null}

      <div id={chartId} ref={containerRef} style={{ width: '100%', minHeight: 300 }} />

      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <button
          className="export-btn"
          onClick={() => {
            const el = document.getElementById(chartId);
            if (el && window.Plotly) {
              window.Plotly.downloadImage(el, {
                format: 'svg',
                filename: (chartSpec?.layout?.title?.text || 'chart').replace(/[^a-zA-Z0-9]/g, '_'),
              });
            }
          }}
          style={{
            background: '#f8f8f8',
            border: '1px solid #e0e0e0',
            borderRadius: 6,
            padding: '5px 12px',
            cursor: 'pointer',
            fontSize: 11,
            color: '#666',
            fontWeight: 600,
            fontFamily: "'Montserrat', sans-serif",
          }}
        >
          Download SVG
        </button>
        <button
          className="export-btn"
          onClick={() => {
            const el = document.getElementById(chartId);
            if (el && window.Plotly) {
              window.Plotly.downloadImage(el, {
                format: 'png',
                width: 1200,
                height: 600,
                filename: (chartSpec?.layout?.title?.text || 'chart').replace(/[^a-zA-Z0-9]/g, '_'),
              });
            }
          }}
          style={{
            background: '#f8f8f8',
            border: '1px solid #e0e0e0',
            borderRadius: 6,
            padding: '5px 12px',
            cursor: 'pointer',
            fontSize: 11,
            color: '#666',
            fontWeight: 600,
            fontFamily: "'Montserrat', sans-serif",
          }}
        >
          Download PNG
        </button>
      </div>
    </div>
  );
}

import React from 'react';

export default function ToolBadges({ tools, details }) {
  if (!tools || tools.length === 0) return null;
  const icons = {
    query_workitems: '🔍',
    search_workitems: '🧠',
    search_website: '🌐',
    analyze_patterns: '📊',
    generate_user_stories: '📋',
    query_hierarchy: '🔗',
    compute_kpi: '📈',
    generate_chart: '📉',
    generate_file: '💾',
  };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
      {tools.map((tool, idx) => (
        <span
          key={`${tool}-${idx}`}
          className="tool-badge"
          title={details && details[idx] ? JSON.stringify(details[idx]).slice(0, 200) : tool}
        >
          <span>{icons[tool] || '⚙️'}</span>
          <span>{String(tool).replace(/_/g, ' ')}</span>
        </span>
      ))}
    </div>
  );
}

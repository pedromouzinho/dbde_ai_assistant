export function renderPlotlyChart(containerOrId, chartSpec) {
  if (!window.Plotly || !chartSpec) return;

  const el = typeof containerOrId === 'string'
    ? document.getElementById(containerOrId)
    : containerOrId;
  if (!el) return;

  const defaultLayout = {
    font: { family: 'Montserrat, sans-serif', size: 12 },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    margin: { l: 50, r: 20, t: 40, b: 40 },
    height: 350,
    colorway: ['#DE3163', '#2196F3', '#4CAF50', '#FF9800', '#9C27B0', '#00BCD4', '#F44336', '#8BC34A'],
  };

  const defaultConfig = {
    displayModeBar: true,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    displaylogo: false,
    responsive: true,
    toImageButtonOptions: {
      format: 'svg',
      filename: chartSpec?.layout?.title?.text || 'chart',
    },
  };

  const layout = { ...defaultLayout, ...(chartSpec.layout || {}) };
  const config = { ...defaultConfig, ...(chartSpec.config || {}) };

  try {
    if (el.data) {
      window.Plotly.react(el, chartSpec.data || [], layout, config);
    } else {
      window.Plotly.newPlot(el, chartSpec.data || [], layout, config);
    }
  } catch (e) {
    console.warn('[Chart] Plotly render error, retrying with newPlot:', e);
    window.Plotly.newPlot(el, chartSpec.data || [], layout, config);
  }
}

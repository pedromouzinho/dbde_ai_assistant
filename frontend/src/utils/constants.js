export const API_URL = window.location.origin;
export const DEFAULT_MAX_IMAGES_PER_MESSAGE = 10;
export const DEFAULT_MAX_FILES_PER_CONVERSATION = 10;
export const DEFAULT_MAX_BATCH_TOTAL_BYTES = 60 * 1024 * 1024;
export const UPLOAD_POLL_INTERVAL_MS = 1200;
export const UPLOAD_JOB_TIMEOUT_MS = 20 * 60 * 1000;
export const DEVOPS_WORKITEM_BASE_URL = 'https://dev.azure.com/ptbcp/IT.DIT/_workitems/edit/';
export const EMPTY_CONVERSATION = { id: null, title: 'Nova conversa', messages: [], mode: 'general', uploadedFiles: [] };

export const MILLENNIUM_LOGO_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="16" fill="#1A1A1A"/>
  <text x="50" y="72" font-size="64" font-weight="800" fill="#DE3163"
        text-anchor="middle" font-family="'Montserrat', sans-serif"
        letter-spacing="-2">M</text>
</svg>`;
export const MILLENNIUM_LOGO_DATA_URI = `data:image/svg+xml,${encodeURIComponent(MILLENNIUM_LOGO_SVG)}`;

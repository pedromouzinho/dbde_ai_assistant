import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  root: 'frontend',
  plugins: [react()],
  build: {
    outDir: '../static',
    emptyOutDir: false,
    rollupOptions: {
      input: resolve(__dirname, 'frontend/index.html'),
      output: {
        entryFileNames: 'dist/assets/[name]-[hash].js',
        chunkFileNames: 'dist/assets/[name]-[hash].js',
        assetFileNames: 'dist/assets/[name]-[hash].[ext]',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
    },
  },
});

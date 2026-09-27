import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  return {
    server: { proxy: { '/api': env.API_PROXY_TARGET || 'http://127.0.0.1:8000' } },
    // Map engine and React change far less often than the app: separate files cache between releases.
    build: { rollupOptions: { output: { manualChunks(id: string) {
      if (/node_modules[\/](ol|rbush|quickselect|earcut|color-(space|parse|rgba|name))[\/]/.test(id)) return 'map';
      if (/node_modules[\/](react|react-dom|scheduler)[\/]/.test(id)) return 'react';
    } } } },
  };
});

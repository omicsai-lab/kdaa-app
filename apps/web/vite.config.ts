import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1', port: 5183, strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8183',
      '/docs': 'http://127.0.0.1:8183',
      '/openapi.json': 'http://127.0.0.1:8183'
    }
  },
  // No React plugin or extra UI framework is necessary for this small client.
  esbuild: { jsx: 'automatic' },
});

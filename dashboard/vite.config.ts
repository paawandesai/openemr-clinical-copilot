import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// In dev, Vite serves the SPA on :5173 and forwards /auth and /fhir to the
// BFF on :8787, so the browser still sees one origin (PUBLIC_ORIGIN must
// then be http://localhost:5173). In production the BFF serves dist/web.
export default defineConfig({
  root: 'web',
  plugins: [react()],
  build: {
    outDir: '../dist/web',
    emptyOutDir: true,
    // Never inline assets as data: URIs; the CSP allows fonts only from 'self'.
    assetsInlineLimit: 0,
    sourcemap: false,
  },
  css: {
    preprocessorOptions: {
      // Bootstrap 4.6's SCSS predates Dart Sass 2 and warns on every build.
      scss: { quietDeps: true, silenceDeprecations: ['import', 'global-builtin', 'color-functions', 'slash-div', 'legacy-js-api'] },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/auth': 'http://127.0.0.1:8787',
      '/fhir': 'http://127.0.0.1:8787',
    },
  },
  test: {
    root: '.',
    include: ['test/unit/**/*.test.{ts,tsx}'],
    environment: 'node',
  },
});

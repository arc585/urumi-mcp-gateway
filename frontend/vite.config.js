import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In the cluster the Ingress routes /api to the gateway. During `npm run dev` this proxy plays that
// role, forwarding /api to the gateway running in Kind.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: 'http://app.urumi.test:8880', changeOrigin: true },
    },
  },
});

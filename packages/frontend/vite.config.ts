import { defineConfig } from 'vite';
import solid from 'vite-plugin-solid';

export default defineConfig({
  plugins: [solid()],
  server: {
    port: 3000,
    host: true,
  },
  build: {
    target: 'esnext',
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: true,
  },
  define: {
    // Replace at build time with actual API Gateway URL
    __API_BASE_URL__: JSON.stringify(process.env.VITE_API_BASE_URL || 'https://api.fdnix.com/v1'),
  },
});
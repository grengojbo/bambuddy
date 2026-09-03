import { mergeConfig } from 'vite'
import baseConfig from './vite.config'

// Dev-server overrides for running vite inside the compose stack. Kept in its
// own file rather than patched into vite.config.ts, which is upstream's and
// would then conflict on every sync.
//
// Used as: npm run dev -- --config vite.config.dev.ts

// Inside the container `localhost` is the container itself, so the upstream
// config's proxy target has to be repointed at the backend service.
const backendUrl = process.env.BACKEND_URL || 'http://bambuddy:8000'

// Set when the stack is published through the Tailscale sidecar, e.g.
// bambuddy-dev.tailnet-name.ts.net.
const tsHostname = process.env.TS_HOSTNAME || ''

export default mergeConfig(baseConfig, {
  server: {
    port: 5173,
    // Bind mounts on macOS do not deliver filesystem events into the VM, so
    // vite's watcher sees nothing without polling. 300 ms is a fair trade
    // between latency and idle CPU on a tree this size.
    watch: {
      usePolling: true,
      interval: 300,
    },
    // Vite refuses requests whose Host header it does not recognise. The
    // tailnet name is a different host than localhost, so it has to be named.
    allowedHosts: tsHostname ? [tsHostname] : [],
    proxy: {
      '/api/v1/ws': {
        target: backendUrl,
        ws: true,
        changeOrigin: true,
      },
      '/api': {
        target: backendUrl,
        changeOrigin: true,
      },
    },
    // Over Tailscale the page is served on https://…:443, so the HMR socket
    // must be wss on 443 as well — the default would point the browser at
    // ws://<tailnet host>:5173, which nothing is listening on.
    ...(tsHostname
      ? {
          hmr: {
            protocol: 'wss',
            host: tsHostname,
            clientPort: 443,
          },
        }
      : {}),
  },
})

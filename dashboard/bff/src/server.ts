import { serve } from '@hono/node-server';
import { createApp } from './app.js';
import { loadConfig } from './config.js';
import { logEvent } from './log.js';
import { upstreamFetch } from './upstream.js';

const config = loadConfig();
const { app } = createApp({ config, fetch: upstreamFetch() });

serve({ fetch: app.fetch, port: config.port, hostname: config.host }, () => {
  logEvent({ event: 'started' });
});

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/dashboard.scss';
// Font Awesome Free 6.7.2, the release OpenEMR ships; bundled, never from a CDN.
import '@fortawesome/fontawesome-free/css/fontawesome.min.css';
import '@fortawesome/fontawesome-free/css/solid.min.css';
import { App } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

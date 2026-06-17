import React from 'react';
import ReactDOM from 'react-dom/client';
import './monaco-setup';   // use bundled monaco (not the CDN) — must run before any editor mounts
import App from './App';
import './styles/tokens.css';
import './styles/global.css';
import './styles/agent-chat.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

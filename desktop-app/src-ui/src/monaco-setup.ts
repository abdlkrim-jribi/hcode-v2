/**
 * Monaco setup — point @monaco-editor/react at the BUNDLED monaco-editor instead
 * of its default CDN loader (https://cdn.jsdelivr.net/npm/monaco-editor).
 *
 * The production Tauri build runs offline under a strict CSP (`script-src 'self'`),
 * so the CDN loader is blocked and the file-viewer editor would be blank. The
 * monaco-editor package is already in the bundle (DiffReviewer imports it
 * directly); reusing it here keeps the editor self-hosted and CSP-clean — no CSP
 * widening needed for the loader itself.
 *
 * Import this for its side effect BEFORE the app renders (see main.tsx).
 */
import { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';

loader.config({ monaco });

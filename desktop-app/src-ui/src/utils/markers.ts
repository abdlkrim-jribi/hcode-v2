/**
 * PEV protocol markers — mirrors the CLI's cli/live.py `_strip_markers`.
 *
 * The agent emits control markers (PLAN COMPLETE, EXECUTION COMPLETE,
 * VERIFIED OK, ISSUES FOUND) inline in its text so the PEV middleware can
 * detect phase transitions. They must NEVER be shown to the user — the chat
 * UI strips them from any displayed plan/answer/verdict text.
 */
const MARKERS = ['PLAN COMPLETE', 'EXECUTION COMPLETE', 'VERIFIED OK', 'ISSUES FOUND'];

/**
 * Remove PEV markers from user-facing text and tidy whitespace.
 * Applied at render time so the raw stream stays intact in state.
 */
export function stripMarkers(text: string): string {
    if (!text) return '';
    let out = text;
    for (const marker of MARKERS) {
        out = out.replace(new RegExp(marker, 'gi'), '');
    }
    return out
        .split('\n')
        .map(line => line.replace(/\s+$/, ''))   // rstrip each line
        .join('\n')
        .replace(/\n{3,}/g, '\n\n')              // collapse runs of blank lines
        .trim();
}

/**
 * SettingsPanel — Organized, searchable settings like VS Code.
 * Phase 2.5: Task 5 (Settings Reorganization).
 * Categories: General, Agent, Safety, About.
 */
import React, { useState, useCallback, useMemo } from 'react';
import * as ipc from '../ipc/bridge';

type Provider = 'openai' | 'anthropic' | 'cerebras';

// ── Settings State ──────────────────────────────────────────────────────────

interface EditorSettings {
    fontSize: number;
    wordWrap: boolean;
    minimap: boolean;
    lineNumbers: boolean;
    tabSize: number;
}

interface SettingsPanelProps {
    theme?: 'dark' | 'light';
    onThemeChange?: (theme: 'dark' | 'light') => void;
    editorSettings?: EditorSettings;
    onEditorSettingsChange?: (settings: EditorSettings) => void;
}

const DEFAULT_EDITOR: EditorSettings = {
    fontSize: 13,
    wordWrap: false,
    minimap: true,
    lineNumbers: true,
    tabSize: 4,
};

// ── Component ───────────────────────────────────────────────────────────────

export default function SettingsPanel({ theme = 'dark', onThemeChange, editorSettings, onEditorSettingsChange }: SettingsPanelProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [autonomousMode, setAutonomousMode] = useState(false);
    const [provider, setProvider] = useState<string>('auto');
    const [keys, setKeys] = useState<Record<Provider, string>>({ openai: '', anthropic: '', cerebras: '' });
    const [saved, setSaved] = useState<Record<Provider, boolean>>({ openai: false, anthropic: false, cerebras: false });
    const [remoteLlmEnabled, setRemoteLlmEnabled] = useState(false);
    const [showConsentModal, setShowConsentModal] = useState(false);

    const editor = editorSettings || DEFAULT_EDITOR;

    const updateEditor = useCallback((partial: Partial<EditorSettings>) => {
        onEditorSettingsChange?.({ ...editor, ...partial });
    }, [editor, onEditorSettingsChange]);

    const saveKey = useCallback(async (p: Provider) => {
        await ipc.saveApiKey(p, keys[p]);
        setSaved((prev) => ({ ...prev, [p]: true }));
        setTimeout(() => setSaved((prev) => ({ ...prev, [p]: false })), 2000);
    }, [keys]);

    const handleRemoteLlmToggle = useCallback((enabled: boolean) => {
        if (enabled) {
            setShowConsentModal(true);
        } else {
            setRemoteLlmEnabled(false);
        }
    }, []);

    const confirmRemoteLlm = useCallback(() => {
        setRemoteLlmEnabled(true);
        setShowConsentModal(false);
    }, []);

    // ── Category visibility based on search ─────────────────────────────────
    const q = searchQuery.toLowerCase();
    const show = useCallback((text: string) => {
        if (!q) return true;
        return text.toLowerCase().includes(q);
    }, [q]);

    const showGeneral = useMemo(() => show('general theme font size color appearance'), [show]);
    const showEditor = useMemo(() => show('editor word wrap minimap line numbers tab size font'), [show]);
    const showAgent = useMemo(() => show('agent provider api key openai anthropic cerebras remote llm'), [show]);
    const showSafety = useMemo(() => show('safety autonomous mode auto approve'), [show]);

    return (
        <div className="hcode-settings">
            {/* Search */}
            <div className="hcode-settings__search-wrap">
                <input
                    type="text"
                    className="hcode-settings__search"
                    placeholder="Search settings..."
                    value={searchQuery}
                    onChange={e => setSearchQuery(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                />
                {searchQuery && (
                    <button
                        className="hcode-settings__search-clear"
                        onClick={() => setSearchQuery('')}
                    >
                        ✕
                    </button>
                )}
            </div>

            {/* ── General ──────────────────────────────────────────────────────── */}
            {showGeneral && (
                <section className="hcode-settings-section">
                    <h2 className="hcode-settings-section__title">
                        <span className="hcode-settings-section__icon">◐</span>
                        General
                    </h2>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Color Theme</span>
                            <span className="hcode-settings-row__desc">Controls the overall appearance</span>
                        </div>
                        <select
                            className="hcode-select hcode-select--small"
                            value={theme}
                            onChange={e => onThemeChange?.(e.target.value as 'dark' | 'light')}
                        >
                            <option value="dark">Dark</option>
                            <option value="light">Light</option>
                        </select>
                    </div>
                </section>
            )}

            {/* ── Editor ───────────────────────────────────────────────────────── */}
            {showEditor && (
                <section className="hcode-settings-section">
                    <h2 className="hcode-settings-section__title">
                        <span className="hcode-settings-section__icon">⌘</span>
                        Editor
                    </h2>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Font Size</span>
                            <span className="hcode-settings-row__desc">Controls the font size in pixels</span>
                        </div>
                        <input
                            type="number"
                            className="hcode-input hcode-input--small"
                            value={editor.fontSize}
                            min={10}
                            max={30}
                            onChange={e => updateEditor({ fontSize: parseInt(e.target.value) || 13 })}
                            style={{ width: '60px' }}
                        />
                    </div>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Tab Size</span>
                            <span className="hcode-settings-row__desc">Number of spaces per indentation level</span>
                        </div>
                        <select
                            className="hcode-select hcode-select--small"
                            value={editor.tabSize}
                            onChange={e => updateEditor({ tabSize: parseInt(e.target.value) })}
                        >
                            <option value={2}>2</option>
                            <option value={4}>4</option>
                            <option value={8}>8</option>
                        </select>
                    </div>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Word Wrap</span>
                            <span className="hcode-settings-row__desc">Wrap lines at viewport width</span>
                        </div>
                        <label className="hcode-toggle">
                            <input type="checkbox" checked={editor.wordWrap} onChange={e => updateEditor({ wordWrap: e.target.checked })} />
                            <span className="hcode-toggle__track"></span>
                        </label>
                    </div>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Minimap</span>
                            <span className="hcode-settings-row__desc">Show a code overview on the right side</span>
                        </div>
                        <label className="hcode-toggle">
                            <input type="checkbox" checked={editor.minimap} onChange={e => updateEditor({ minimap: e.target.checked })} />
                            <span className="hcode-toggle__track"></span>
                        </label>
                    </div>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Line Numbers</span>
                            <span className="hcode-settings-row__desc">Show line numbers in the gutter</span>
                        </div>
                        <label className="hcode-toggle">
                            <input type="checkbox" checked={editor.lineNumbers} onChange={e => updateEditor({ lineNumbers: e.target.checked })} />
                            <span className="hcode-toggle__track"></span>
                        </label>
                    </div>
                </section>
            )}

            {/* ── Agent / AI Provider ──────────────────────────────────────────── */}
            {showAgent && (
                <section className="hcode-settings-section">
                    <h2 className="hcode-settings-section__title">
                        <span className="hcode-settings-section__icon">◆</span>
                        AI Provider
                    </h2>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Enable Remote LLM</span>
                            <span className="hcode-settings-row__desc">Send code context to a remote AI provider</span>
                        </div>
                        <label className="hcode-toggle">
                            <input type="checkbox" checked={remoteLlmEnabled} onChange={e => handleRemoteLlmToggle(e.target.checked)} />
                            <span className="hcode-toggle__track"></span>
                        </label>
                    </div>

                    {/* Consent Modal */}
                    {showConsentModal && (
                        <div className="hcode-banner hcode-banner--warning">
                            <p><strong>Remote AI Provider Consent</strong></p>
                            <p className="hcode-hint">
                                Enabling this will send code context snippets (not entire files) to the selected AI provider's API.
                                Your API keys are stored securely in the OS credential manager and are never shared.
                            </p>
                            <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
                                <button className="hcode-btn hcode-btn--primary hcode-btn--small" onClick={confirmRemoteLlm}>I Understand, Enable</button>
                                <button className="hcode-btn hcode-btn--secondary hcode-btn--small" onClick={() => setShowConsentModal(false)}>Cancel</button>
                            </div>
                        </div>
                    )}

                    {remoteLlmEnabled && (
                        <>
                            <div className="hcode-settings-row">
                                <div className="hcode-settings-row__info">
                                    <span className="hcode-settings-row__label">Provider</span>
                                    <span className="hcode-settings-row__desc">Select the AI backend to use</span>
                                </div>
                                <select
                                    className="hcode-select hcode-select--small"
                                    value={provider}
                                    onChange={e => setProvider(e.target.value)}
                                >
                                    <option value="auto">Auto (best available)</option>
                                    <option value="openai">OpenAI (GPT)</option>
                                    <option value="anthropic">Anthropic (Claude)</option>
                                    <option value="cerebras">Cerebras</option>
                                </select>
                            </div>

                            {/* API Keys */}
                            <div className="hcode-settings-divider" />
                            <p className="hcode-hint" style={{ marginBottom: 'var(--space-2)' }}>
                                Keys are stored securely in the OS credential manager.
                            </p>
                            {(['openai', 'anthropic', 'cerebras'] as Provider[]).map(p => (
                                <div key={p} className="hcode-settings-row hcode-settings-row--stacked">
                                    <label className="hcode-settings-row__label">
                                        {p.charAt(0).toUpperCase() + p.slice(1)} API Key
                                    </label>
                                    <div className="hcode-key-input-row">
                                        <input
                                            type="password"
                                            className="hcode-input"
                                            placeholder={`Enter ${p} key...`}
                                            value={keys[p]}
                                            onChange={e => setKeys(prev => ({ ...prev, [p]: e.target.value }))}
                                        />
                                        <button
                                            className="hcode-btn hcode-btn--secondary hcode-btn--small"
                                            onClick={() => saveKey(p)}
                                            disabled={!keys[p]}
                                        >
                                            {saved[p] ? '● Saved' : 'Save'}
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </>
                    )}
                </section>
            )}

            {/* ── Safety ───────────────────────────────────────────────────────── */}
            {showSafety && (
                <section className="hcode-settings-section">
                    <h2 className="hcode-settings-section__title">
                        <span className="hcode-settings-section__icon">⊘</span>
                        Safety
                    </h2>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Autonomous Mode</span>
                            <span className="hcode-settings-row__desc">Execute plans without manual approval</span>
                        </div>
                        <label className="hcode-toggle">
                            <input type="checkbox" checked={autonomousMode} onChange={e => setAutonomousMode(e.target.checked)} />
                            <span className="hcode-toggle__track"></span>
                        </label>
                    </div>

                    {autonomousMode && (
                        <div className="hcode-banner hcode-banner--warning">
                            <strong>Autonomous mode enabled.</strong> Hcode will execute plans without waiting for approval.
                        </div>
                    )}
                </section>
            )}

            {/* ── About ────────────────────────────────────────────────────────── */}
            {show('about version keyboard shortcuts') && (
                <section className="hcode-settings-section">
                    <h2 className="hcode-settings-section__title">
                        <span className="hcode-settings-section__icon">ⓘ</span>
                        About
                    </h2>

                    <div className="hcode-settings-row">
                        <div className="hcode-settings-row__info">
                            <span className="hcode-settings-row__label">Version</span>
                        </div>
                        <span className="hcode-settings-row__value">0.1.0-alpha</span>
                    </div>

                    <div className="hcode-settings-divider" />

                    <div className="hcode-shortcuts-grid">
                        <div className="hcode-shortcuts-grid__title">Keyboard Shortcuts</div>
                        {[
                            ['Ctrl+Shift+P', 'Command Palette'],
                            ['Ctrl+O', 'Open Folder'],
                            ['Ctrl+B', 'Toggle Explorer'],
                            ['Ctrl+J', 'Toggle Agent Panel'],
                            ['Ctrl+K', 'New Task'],
                            ['Ctrl+,', 'Settings'],
                            ['Ctrl+1/2/3', 'Focus Panel'],
                            ['Escape', 'Close / Back to Editor'],
                        ].map(([key, desc]) => (
                            <div key={key} className="hcode-shortcuts-grid__row">
                                <kbd className="hcode-kbd">{key}</kbd>
                                <span>{desc}</span>
                            </div>
                        ))}
                    </div>
                </section>
            )}

            {/* No results */}
            {searchQuery && !showGeneral && !showEditor && !showAgent && !showSafety && (
                <div className="hcode-settings__no-results">
                    No settings matching "{searchQuery}"
                </div>
            )}
        </div>
    );
}

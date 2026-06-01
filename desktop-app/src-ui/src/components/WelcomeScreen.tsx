/**
 * WelcomeScreen — First-run experience shown when no file is open.
 * Phase 2.5: Task 6 (Welcome Screen).
 * Modeled after VS Code/Cursor welcome tab.
 */
import React from 'react';

interface WelcomeScreenProps {
    onOpenFolder: () => void;
    onNewTask: () => void;
    onOpenSettings: () => void;
    recentFolders: string[];
    onOpenRecent: (path: string) => void;
}

export default function WelcomeScreen({ onOpenFolder, onNewTask, onOpenSettings, recentFolders, onOpenRecent }: WelcomeScreenProps) {
    return (
        <div className="hcode-welcome">
            {/* Hero */}
            <div className="hcode-welcome__hero">
                <div className="hcode-welcome__logo">H</div>
                <h1 className="hcode-welcome__title">Hcode</h1>
                <p className="hcode-welcome__subtitle">AI-Powered Coding Agent</p>
            </div>

            {/* Quick Actions */}
            <div className="hcode-welcome__section">
                <h2 className="hcode-welcome__section-title">Start</h2>
                <div className="hcode-welcome__actions">
                    <button className="hcode-welcome__action" onClick={onOpenFolder}>
                        <span className="hcode-welcome__action-icon">⊞</span>
                        <div className="hcode-welcome__action-info">
                            <span className="hcode-welcome__action-label">Open Folder</span>
                            <span className="hcode-welcome__action-hint">Ctrl+O</span>
                        </div>
                    </button>
                    <button className="hcode-welcome__action" onClick={onNewTask}>
                        <span className="hcode-welcome__action-icon">◆</span>
                        <div className="hcode-welcome__action-info">
                            <span className="hcode-welcome__action-label">New Task</span>
                            <span className="hcode-welcome__action-hint">Ctrl+K</span>
                        </div>
                    </button>
                    <button className="hcode-welcome__action" onClick={onOpenSettings}>
                        <span className="hcode-welcome__action-icon">⚙</span>
                        <div className="hcode-welcome__action-info">
                            <span className="hcode-welcome__action-label">Settings</span>
                            <span className="hcode-welcome__action-hint">Ctrl+,</span>
                        </div>
                    </button>
                </div>
            </div>

            {/* Recent Folders */}
            {recentFolders.length > 0 && (
                <div className="hcode-welcome__section">
                    <h2 className="hcode-welcome__section-title">Recent</h2>
                    <div className="hcode-welcome__recent">
                        {recentFolders.slice(0, 5).map(path => (
                            <button
                                key={path}
                                className="hcode-welcome__recent-item"
                                onClick={() => onOpenRecent(path)}
                                title={path}
                            >
                                <span className="hcode-welcome__recent-name">
                                    {path.split(/[\\/]/).pop()}
                                </span>
                                <span className="hcode-welcome__recent-path">{path}</span>
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Keyboard Shortcuts Quick Reference */}
            <div className="hcode-welcome__section">
                <h2 className="hcode-welcome__section-title">Shortcuts</h2>
                <div className="hcode-welcome__shortcuts">
                    {[
                        ['Ctrl+Shift+P', 'Command Palette'],
                        ['Ctrl+B', 'Toggle Explorer'],
                        ['Ctrl+J', 'Toggle Agent'],
                        ['Ctrl+K', 'New Task'],
                    ].map(([key, desc]) => (
                        <div key={key} className="hcode-welcome__shortcut">
                            <kbd className="hcode-kbd">{key}</kbd>
                            <span>{desc}</span>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

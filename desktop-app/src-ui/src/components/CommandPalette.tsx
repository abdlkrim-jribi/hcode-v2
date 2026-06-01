/**
 * CommandPalette — Ctrl+Shift+P fuzzy-search command launcher.
 * Phase 2.5: Task 3 (Command Palette).
 * Modeled after VS Code / Cursor command palette.
 */
import React, { useState, useEffect, useRef, useMemo } from 'react';

// ── Command Definition ──────────────────────────────────────────────────────

export interface Command {
    id: string;
    label: string;
    category?: string;
    shortcut?: string;
    action: () => void;
}

interface CommandPaletteProps {
    isOpen: boolean;
    onClose: () => void;
    commands: Command[];
}

// ── Simple fuzzy match ──────────────────────────────────────────────────────

function fuzzyMatch(query: string, target: string): { matched: boolean; score: number } {
    const q = query.toLowerCase();
    const t = target.toLowerCase();

    // Exact substring match → high score
    if (t.includes(q)) return { matched: true, score: 100 - t.indexOf(q) };

    // Character-by-character fuzzy
    let qi = 0;
    let score = 0;
    for (let ti = 0; ti < t.length && qi < q.length; ti++) {
        if (t[ti] === q[qi]) {
            score += 10;
            // Bonus for consecutive matches
            if (ti > 0 && t[ti - 1] === q[qi - 1]) score += 5;
            qi++;
        }
    }

    return { matched: qi === q.length, score };
}

// ── Component ───────────────────────────────────────────────────────────────

export default function CommandPalette({ isOpen, onClose, commands }: CommandPaletteProps) {
    const [query, setQuery] = useState('');
    const [selectedIndex, setSelectedIndex] = useState(0);
    const inputRef = useRef<HTMLInputElement>(null);
    const listRef = useRef<HTMLDivElement>(null);

    // Filter + sort by fuzzy score
    const results = useMemo(() => {
        if (!query.trim()) return commands;

        return commands
            .map(cmd => {
                const labelMatch = fuzzyMatch(query, cmd.label);
                const catMatch = cmd.category ? fuzzyMatch(query, cmd.category) : { matched: false, score: 0 };
                const bestScore = Math.max(labelMatch.score, catMatch.score);
                return { cmd, matched: labelMatch.matched || catMatch.matched, score: bestScore };
            })
            .filter(r => r.matched)
            .sort((a, b) => b.score - a.score)
            .map(r => r.cmd);
    }, [query, commands]);

    // Reset on open
    useEffect(() => {
        if (isOpen) {
            setQuery('');
            setSelectedIndex(0);
            // Focus input after render
            requestAnimationFrame(() => inputRef.current?.focus());
        }
    }, [isOpen]);

    // Keep selected index in bounds
    useEffect(() => {
        if (selectedIndex >= results.length) {
            setSelectedIndex(Math.max(0, results.length - 1));
        }
    }, [results.length, selectedIndex]);

    // Scroll selected item into view
    useEffect(() => {
        if (listRef.current) {
            const selected = listRef.current.children[selectedIndex] as HTMLElement;
            selected?.scrollIntoView({ block: 'nearest' });
        }
    }, [selectedIndex]);

    const executeCommand = (cmd: Command) => {
        cmd.action();
        onClose();
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        switch (e.key) {
            case 'Escape':
                e.preventDefault();
                onClose();
                break;
            case 'ArrowDown':
                e.preventDefault();
                setSelectedIndex(i => Math.min(i + 1, results.length - 1));
                break;
            case 'ArrowUp':
                e.preventDefault();
                setSelectedIndex(i => Math.max(i - 1, 0));
                break;
            case 'Enter':
                e.preventDefault();
                if (results[selectedIndex]) {
                    executeCommand(results[selectedIndex]);
                }
                break;
        }
    };

    if (!isOpen) return null;

    return (
        <div className="hcode-palette-overlay" onClick={onClose}>
            <div
                className="hcode-palette"
                onClick={e => e.stopPropagation()}
            >
                {/* Search Input */}
                <div className="hcode-palette__input-wrap">
                    <span className="hcode-palette__input-icon">⟩</span>
                    <input
                        ref={inputRef}
                        type="text"
                        className="hcode-palette__input"
                        placeholder="Type a command..."
                        value={query}
                        onChange={e => { setQuery(e.target.value); setSelectedIndex(0); }}
                        onKeyDown={handleKeyDown}
                        autoComplete="off"
                        spellCheck={false}
                    />
                </div>

                {/* Results */}
                <div className="hcode-palette__results" ref={listRef}>
                    {results.map((cmd, i) => (
                        <div
                            key={cmd.id}
                            className={`hcode-palette__item ${i === selectedIndex ? 'hcode-palette__item--selected' : ''}`}
                            onClick={() => executeCommand(cmd)}
                            onMouseEnter={() => setSelectedIndex(i)}
                        >
                            <div className="hcode-palette__item-left">
                                <span className="hcode-palette__item-label">{cmd.label}</span>
                                {cmd.category && (
                                    <span className="hcode-palette__item-category">{cmd.category}</span>
                                )}
                            </div>
                            {cmd.shortcut && (
                                <span className="hcode-palette__item-shortcut">{cmd.shortcut}</span>
                            )}
                        </div>
                    ))}

                    {results.length === 0 && query && (
                        <div className="hcode-palette__empty">
                            No commands matching "{query}"
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

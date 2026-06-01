/**
 * FileExplorer — IDE-grade file tree with open folder, recent folders,
 * file type icons, keyboard navigation, and expand/collapse.
 * Phase 2.5: Tasks 1 & 2 (Open Folder + File Tree Enhancements).
 */
import React, { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import type { FileEntry } from '../types';

// ── File Icon System ────────────────────────────────────────────────────────

const FILE_ICONS: Record<string, { label: string; color: string }> = {
    // Languages
    ts:   { label: 'TS', color: 'hsl(210, 60%, 55%)' },
    tsx:  { label: 'TX', color: 'hsl(210, 60%, 55%)' },
    js:   { label: 'JS', color: 'hsl(48, 80%, 55%)' },
    jsx:  { label: 'JX', color: 'hsl(48, 80%, 55%)' },
    py:   { label: 'PY', color: 'hsl(210, 55%, 50%)' },
    rs:   { label: 'RS', color: 'hsl(25, 80%, 55%)' },
    go:   { label: 'GO', color: 'hsl(190, 60%, 50%)' },
    java: { label: 'JA', color: 'hsl(15, 70%, 55%)' },
    c:    { label: 'C',  color: 'hsl(210, 55%, 55%)' },
    cpp:  { label: 'C+', color: 'hsl(210, 55%, 55%)' },
    rb:   { label: 'RB', color: 'hsl(0, 60%, 55%)' },
    php:  { label: 'PH', color: 'hsl(230, 40%, 55%)' },
    swift:{ label: 'SW', color: 'hsl(15, 80%, 55%)' },
    // Web
    html: { label: 'HT', color: 'hsl(15, 70%, 55%)' },
    css:  { label: 'CS', color: 'hsl(210, 60%, 55%)' },
    scss: { label: 'SC', color: 'hsl(330, 50%, 55%)' },
    vue:  { label: 'VU', color: 'hsl(150, 50%, 45%)' },
    svelte:{ label: 'SV', color: 'hsl(15, 80%, 55%)' },
    // Data / Config
    json: { label: '{}', color: 'hsl(48, 60%, 50%)' },
    toml: { label: 'TM', color: 'hsl(0, 0%, 50%)' },
    yaml: { label: 'YM', color: 'hsl(0, 60%, 55%)' },
    yml:  { label: 'YM', color: 'hsl(0, 60%, 55%)' },
    xml:  { label: 'XM', color: 'hsl(30, 60%, 55%)' },
    env:  { label: '⚙',  color: 'hsl(48, 60%, 50%)' },
    // Docs
    md:   { label: 'MD', color: 'hsl(210, 20%, 55%)' },
    txt:  { label: 'TX', color: 'hsl(0, 0%, 55%)' },
    rst:  { label: 'RS', color: 'hsl(0, 0%, 55%)' },
    // Build / Config
    lock: { label: '⚷',  color: 'hsl(0, 0%, 45%)' },
    // Misc
    sh:   { label: 'SH', color: 'hsl(120, 30%, 45%)' },
    bat:  { label: 'BA', color: 'hsl(120, 30%, 45%)' },
    ps1:  { label: 'PS', color: 'hsl(210, 60%, 55%)' },
    sql:  { label: 'SQ', color: 'hsl(30, 60%, 55%)' },
    graphql: { label: 'GQ', color: 'hsl(320, 50%, 55%)' },
};

function getFileIcon(name: string): { label: string; color: string } {
    const ext = name.split('.').pop()?.toLowerCase() ?? '';
    if (FILE_ICONS[ext]) return FILE_ICONS[ext];
    // Special filenames
    if (name === 'Dockerfile') return { label: '🐳', color: 'hsl(210, 60%, 55%)' };
    if (name === 'Makefile') return { label: 'MK', color: 'hsl(0, 0%, 55%)' };
    if (name.startsWith('.git')) return { label: 'GI', color: 'hsl(15, 60%, 55%)' };
    if (name.includes('requirements')) return { label: '📦', color: 'hsl(120, 40%, 50%)' };
    if (name.includes('package.json')) return { label: 'NM', color: 'hsl(120, 40%, 50%)' };
    return { label: '··', color: 'var(--fg-tertiary)' };
}

// ── Recent Folders ──────────────────────────────────────────────────────────

const RECENT_FOLDERS_KEY = 'hcode-recent-folders';

function getRecentFolders(): string[] {
    try {
        return JSON.parse(localStorage.getItem(RECENT_FOLDERS_KEY) || '[]');
    } catch { return []; }
}

function addToRecentFolders(path: string): void {
    const recent = getRecentFolders();
    const updated = [path, ...recent.filter(p => p !== path)].slice(0, 8);
    localStorage.setItem(RECENT_FOLDERS_KEY, JSON.stringify(updated));
}

// ── Flatten utility for keyboard navigation ─────────────────────────────────

interface FlatItem { entry: FileEntry; depth: number; }

function flattenTree(entries: FileEntry[], expanded: Set<string>, depth = 0): FlatItem[] {
    const result: FlatItem[] = [];
    for (const entry of entries) {
        result.push({ entry, depth });
        if (entry.isDirectory && expanded.has(entry.path) && entry.children) {
            result.push(...flattenTree(entry.children, expanded, depth + 1));
        }
    }
    return result;
}

// ── Props ───────────────────────────────────────────────────────────────────

interface Props {
    workDir: string;
    fileTree: FileEntry[];
    onSelectFolder: () => void;
    onBrowsePath: (path: string) => void;
    onOpenFile: (path: string) => void;
}

// ── TreeNode ────────────────────────────────────────────────────────────────

function TreeNode({ entry, depth, isSelected, onToggle, onSelect, onClick }: {
    entry: FileEntry;
    depth: number;
    isSelected: boolean;
    onToggle: (path: string) => void;
    onSelect: (path: string) => void;
    onClick: (entry: FileEntry) => void;
}) {
    const icon = entry.isDirectory ? null : getFileIcon(entry.name);

    return (
        <div
            className={`hcode-tree-item ${entry.isDirectory ? 'hcode-tree-item--dir' : ''} ${isSelected ? 'hcode-tree-item--selected' : ''}`}
            style={{ paddingLeft: `calc(var(--space-2) + ${depth} * var(--space-3))` }}
            onClick={(e) => {
                e.stopPropagation();
                onSelect(entry.path);
                if (entry.isDirectory) {
                    onToggle(entry.path);
                } else {
                    onClick(entry);
                }
            }}
            title={entry.path}
        >
            {/* Chevron for directories */}
            <span className="hcode-tree-chevron">
                {entry.isDirectory ? (
                    <span className="hcode-tree-arrow">▶</span>
                ) : null}
            </span>

            {/* Icon */}
            {entry.isDirectory ? (
                <span className="hcode-tree-folder-icon">▸</span>
            ) : (
                <span
                    className="hcode-tree-file-icon"
                    style={{ color: icon?.color }}
                >
                    {icon?.label}
                </span>
            )}

            {/* Name */}
            <span className="hcode-tree-name">{entry.name}</span>
        </div>
    );
}

// ── FileExplorer ────────────────────────────────────────────────────────────

export default function FileExplorer({ workDir, fileTree, onSelectFolder, onBrowsePath, onOpenFile }: Props) {
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [selectedPath, setSelectedPath] = useState<string | null>(null);
    const treeRef = useRef<HTMLDivElement>(null);

    // Flatten for keyboard nav
    const flatItems = useMemo(() => flattenTree(fileTree, expanded), [fileTree, expanded]);

    const selectedIndex = useMemo(() => {
        if (!selectedPath) return -1;
        return flatItems.findIndex(fi => fi.entry.path === selectedPath);
    }, [flatItems, selectedPath]);

    // Toggle folder expand/collapse
    const handleToggle = useCallback((path: string) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(path)) {
                next.delete(path);
            } else {
                next.add(path);
                // Trigger lazy-load of children
                onBrowsePath(path);
            }
            return next;
        });
    }, [onBrowsePath]);

    // Select an item
    const handleSelect = useCallback((path: string) => {
        setSelectedPath(path);
    }, []);

    // Open file or toggle folder
    const handleClick = useCallback((entry: FileEntry) => {
        if (!entry.isDirectory) {
            onOpenFile(entry.path);
        }
    }, [onOpenFile]);

    // ── Keyboard navigation ─────────────────────────────────────────────────
    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        const idx = selectedIndex;

        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault();
                if (idx < flatItems.length - 1) {
                    setSelectedPath(flatItems[idx + 1].entry.path);
                }
                break;
            case 'ArrowUp':
                e.preventDefault();
                if (idx > 0) {
                    setSelectedPath(flatItems[idx - 1].entry.path);
                }
                break;
            case 'ArrowRight':
                e.preventDefault();
                if (idx >= 0 && flatItems[idx].entry.isDirectory && !expanded.has(flatItems[idx].entry.path)) {
                    handleToggle(flatItems[idx].entry.path);
                }
                break;
            case 'ArrowLeft':
                e.preventDefault();
                if (idx >= 0 && flatItems[idx].entry.isDirectory && expanded.has(flatItems[idx].entry.path)) {
                    handleToggle(flatItems[idx].entry.path);
                }
                break;
            case 'Enter':
                e.preventDefault();
                if (idx >= 0) {
                    const item = flatItems[idx].entry;
                    if (item.isDirectory) {
                        handleToggle(item.path);
                    } else {
                        onOpenFile(item.path);
                    }
                }
                break;
        }
    }, [selectedIndex, flatItems, expanded, handleToggle, onOpenFile]);

    // Track recent folders
    useEffect(() => {
        if (workDir) addToRecentFolders(workDir);
    }, [workDir]);

    const recentFolders = useMemo(() => getRecentFolders(), [workDir]);

    // ── Render: Empty State ─────────────────────────────────────────────────
    if (!workDir && fileTree.length === 0) {
        return (
            <div className="hcode-explorer-empty-state">
                <div className="hcode-explorer-empty-state__icon">⊞</div>
                <p className="hcode-explorer-empty-state__title">No folder open</p>
                <button
                    className="hcode-btn hcode-btn--primary hcode-btn--small"
                    onClick={onSelectFolder}
                >
                    Open Folder
                </button>
                <span className="hcode-explorer-empty-state__hint">Ctrl+O</span>

                {recentFolders.length > 0 && (
                    <div className="hcode-recent-folders">
                        <div className="hcode-recent-folders__title">Recent</div>
                        {recentFolders.map(path => (
                            <button
                                key={path}
                                className="hcode-recent-folders__item"
                                onClick={() => {
                                    // Simulate opening this folder
                                    // The parent App handles actual loading
                                    onSelectFolder();
                                }}
                                title={path}
                            >
                                <span className="hcode-recent-folders__name">
                                    {path.split(/[\\/]/).pop()}
                                </span>
                                <span className="hcode-recent-folders__path">{path}</span>
                            </button>
                        ))}
                    </div>
                )}
            </div>
        );
    }

    // ── Render: File Tree ────────────────────────────────────────────────────
    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            {workDir && (
                <div className="hcode-explorer-workdir" title={workDir}>
                    <span className="hcode-explorer-workdir__name">{workDir.split(/[\\/]/).pop()}</span>
                    <button
                        className="hcode-btn hcode-btn--ghost hcode-btn--small hcode-explorer-workdir__action"
                        onClick={onSelectFolder}
                        title="Open Different Folder (Ctrl+O)"
                    >
                        ⊞
                    </button>
                </div>
            )}

            {fileTree.length > 0 ? (
                <div
                    className="hcode-tree"
                    ref={treeRef}
                    tabIndex={0}
                    onKeyDown={handleKeyDown}
                >
                    {flatItems.map(({ entry, depth }) => {
                        const isExpanded = entry.isDirectory && expanded.has(entry.path);
                        return (
                            <div key={entry.path} className="hcode-tree-node">
                                <TreeNode
                                    entry={entry}
                                    depth={depth}
                                    isSelected={selectedPath === entry.path}
                                    onToggle={handleToggle}
                                    onSelect={handleSelect}
                                    onClick={handleClick}
                                />
                                {/* Expanded indicator via CSS class on the arrow */}
                                {isExpanded && (
                                    <style>{`.hcode-tree-node:has([title="${entry.path}"]) .hcode-tree-arrow { transform: rotate(90deg); }`}</style>
                                )}
                            </div>
                        );
                    })}
                </div>
            ) : (
                <div className="hcode-explorer-empty">
                    {workDir ? 'Loading files...' : 'No project loaded.'}
                </div>
            )}
        </div>
    );
}

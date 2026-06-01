/**
 * MonacoEditor — standalone Monaco editor wrapper for the desktop app.
 * Phase 2.5: Task 8 (Editor Enhancements).
 * Uses @monaco-editor/react for seamless React integration.
 * Supports configurable font size, word wrap, minimap, line numbers, tab size.
 */
import React, { useCallback, useRef } from 'react';
import Editor, { type OnMount } from '@monaco-editor/react';
import type { editor } from 'monaco-editor';

interface EditorSettings {
    fontSize: number;
    wordWrap: boolean;
    minimap: boolean;
    lineNumbers: boolean;
    tabSize: number;
}

interface Props {
    filePath: string;
    content: string;
    onChange?: (content: string) => void;
    readOnly?: boolean;
    theme?: 'dark' | 'light';
    settings?: EditorSettings;
}

const DEFAULT_SETTINGS: EditorSettings = {
    fontSize: 13,
    wordWrap: false,
    minimap: true,
    lineNumbers: true,
    tabSize: 4,
};

function getLanguage(filePath: string): string {
    const ext = filePath.split('.').pop()?.toLowerCase() || '';
    const map: Record<string, string> = {
        py: 'python', ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
        json: 'json', md: 'markdown', css: 'css', html: 'html', xml: 'xml',
        yml: 'yaml', yaml: 'yaml', toml: 'toml', sh: 'shell', bat: 'bat', ps1: 'powershell',
        rs: 'rust', go: 'go', java: 'java', c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
        sql: 'sql', dockerfile: 'dockerfile', scss: 'scss', less: 'less',
        graphql: 'graphql', swift: 'swift', kotlin: 'kotlin', rb: 'ruby', php: 'php',
        vue: 'html', svelte: 'html',
    };
    return map[ext] || 'plaintext';
}

export default function MonacoEditor({ filePath, content, onChange, readOnly = false, theme = 'dark', settings }: Props) {
    const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
    const s = settings || DEFAULT_SETTINGS;

    const handleMount: OnMount = useCallback((editor) => {
        editorRef.current = editor;
        editor.focus();
    }, []);

    const handleChange = useCallback((value: string | undefined) => {
        if (onChange && value !== undefined) {
            onChange(value);
        }
    }, [onChange]);

    return (
        <Editor
            height="100%"
            language={getLanguage(filePath)}
            value={content}
            theme={theme === 'light' ? 'vs' : 'vs-dark'}
            onChange={handleChange}
            onMount={handleMount}
            options={{
                readOnly,
                fontSize: s.fontSize,
                fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Fira Code', monospace",
                fontLigatures: true,
                minimap: { enabled: s.minimap, scale: 1 },
                smoothScrolling: true,
                cursorBlinking: 'smooth',
                cursorSmoothCaretAnimation: 'on',
                padding: { top: 8 },
                bracketPairColorization: { enabled: true },
                renderLineHighlight: 'all',
                scrollBeyondLastLine: false,
                automaticLayout: true,
                wordWrap: s.wordWrap ? 'on' : 'off',
                tabSize: s.tabSize,
                lineNumbers: s.lineNumbers ? 'on' : 'off',
                guides: {
                    bracketPairs: true,
                    indentation: true,
                },
                stickyScroll: { enabled: true },
            }}
        />
    );
}

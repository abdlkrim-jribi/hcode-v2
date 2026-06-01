/**
 * SkillsPanel — lists skills from the v2 daemon (list_skills JSON-RPC method).
 * Falls back to empty list; VITE_MOCK mode returns mock fixtures via bridge.
 */
import { useEffect, useMemo, useState } from 'react';
import * as ipc from '../ipc/bridge';

export interface SkillInfo {
    name: string;
    description: string;
    category: string;
    lastUsed: string | null;
}

interface Props {
    activeSkill?: string | null;
    onSelect?: (name: string) => void;
}

export default function SkillsPanel({ activeSkill = null, onSelect }: Props) {
    const [skills, setSkills] = useState<SkillInfo[]>([]);
    const [query, setQuery]   = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        ipc.listSkills()
            .then(s => setSkills(s as SkillInfo[]))
            .catch(() => setSkills([]))
            .finally(() => setLoading(false));
    }, []);

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return skills;
        return skills.filter(s =>
            s.name.toLowerCase().includes(q) ||
            s.description.toLowerCase().includes(q) ||
            s.category.toLowerCase().includes(q)
        );
    }, [skills, query]);

    return (
        <div className="hcode-skills-panel" style={{ padding: 'var(--space-3)' }}>
            <header style={{ marginBottom: 'var(--space-3)' }}>
                <h3 style={{ margin: 0, fontSize: 'var(--text-sm)' }}>Skills</h3>
                <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)' }}>
                    Loaded from <code>.hcode/skills/</code> via daemon.
                </p>
            </header>

            <input
                type="search"
                placeholder="Filter skills…"
                value={query}
                onChange={e => setQuery(e.target.value)}
                style={{
                    width: '100%', padding: 'var(--space-1) var(--space-2)',
                    marginBottom: 'var(--space-2)', border: '1px solid var(--border-default)',
                    background: 'var(--surface-2)', color: 'var(--fg-primary)',
                    fontSize: 'var(--text-xs)', borderRadius: 3,
                }}
            />

            {loading && <div style={{ color: 'var(--fg-secondary)', fontSize: 'var(--text-xs)' }}>Loading…</div>}

            {!loading && filtered.length === 0 && (
                <div style={{ color: 'var(--fg-secondary)', fontSize: 'var(--text-xs)' }}>
                    No skills found. Drop a <code>SKILL.md</code> in <code>.hcode/skills/&lt;name&gt;/</code>.
                </div>
            )}

            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {filtered.map(skill => {
                    const isActive = skill.name === activeSkill;
                    return (
                        <li
                            key={skill.name}
                            onClick={() => onSelect?.(skill.name)}
                            style={{
                                padding: 'var(--space-2)', marginBottom: 'var(--space-1)',
                                border: `1px solid ${isActive ? 'var(--accent)' : 'var(--border-default)'}`,
                                borderRadius: 4, cursor: 'pointer',
                                background: isActive ? 'var(--surface-3)' : 'var(--surface-2)',
                            }}
                        >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <span style={{ fontWeight: 500, fontSize: 'var(--text-xs)' }}>{skill.name}</span>
                                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)' }}>{skill.category}</span>
                            </div>
                            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)', marginTop: 2 }}>
                                {skill.description}
                            </div>
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}

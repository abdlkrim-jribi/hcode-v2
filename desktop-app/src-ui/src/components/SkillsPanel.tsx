/**
 * SkillsPanel — multi-select skill browser.
 *
 * Each skill can be toggled on/off via a checkbox. Default = all selected
 * (represented as null in the parent). A "Select All" affordance resets to null.
 * The selection controls which skills the agent loads on the next run_task call.
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
    /** null = all selected (default). A Set = only those skills are active. */
    activeSkills?: Set<string> | null;
    /** Called when the user toggles one skill. Receives the skill name and the
     *  full list of all skill names so the parent can convert null→full-set. */
    onToggleSkill?: (name: string, allNames: string[]) => void;
    /** Called when the user clicks "Select All" — parent resets to null. */
    onSelectAll?: () => void;
}

export default function SkillsPanel({ activeSkills = null, onToggleSkill, onSelectAll }: Props) {
    const [skills, setSkills] = useState<SkillInfo[]>([]);
    const [query, setQuery]   = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        ipc.listSkills()
            .then(s => setSkills(s as SkillInfo[]))
            .catch(() => setSkills([]))
            .finally(() => setLoading(false));
    }, []);

    const allNames = useMemo(() => skills.map(s => s.name), [skills]);
    const isAllSelected = activeSkills === null || activeSkills.size === allNames.length;

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return skills;
        return skills.filter(s =>
            s.name.toLowerCase().includes(q) ||
            s.description.toLowerCase().includes(q) ||
            s.category.toLowerCase().includes(q)
        );
    }, [skills, query]);

    const selectedCount = activeSkills === null ? allNames.length : activeSkills.size;

    return (
        <div className="hcode-skills-panel" style={{ padding: 'var(--space-3)' }}>
            <header style={{ marginBottom: 'var(--space-3)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h3 style={{ margin: 0, fontSize: 'var(--text-sm)' }}>Skills</h3>
                    {!isAllSelected && (
                        <button
                            onClick={onSelectAll}
                            style={{
                                fontSize: 'var(--text-xs)', padding: '2px 8px',
                                border: '1px solid var(--border-default)',
                                background: 'var(--surface-3)', color: 'var(--fg-secondary)',
                                borderRadius: 3, cursor: 'pointer',
                            }}
                        >
                            Select All
                        </button>
                    )}
                </div>
                <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)' }}>
                    {isAllSelected
                        ? 'All skills active. Uncheck to limit the agent.'
                        : `${selectedCount} of ${allNames.length} skill${allNames.length !== 1 ? 's' : ''} active.`}
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
                    fontSize: 'var(--text-xs)', borderRadius: 3, boxSizing: 'border-box',
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
                    const isChecked = activeSkills === null || activeSkills.has(skill.name);
                    return (
                        <li
                            key={skill.name}
                            style={{
                                padding: 'var(--space-2)', marginBottom: 'var(--space-1)',
                                border: `1px solid ${isChecked ? 'var(--accent)' : 'var(--border-default)'}`,
                                borderRadius: 4,
                                background: isChecked ? 'var(--surface-3)' : 'var(--surface-2)',
                                opacity: isChecked ? 1 : 0.55,
                            }}
                        >
                            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)', cursor: 'pointer' }}>
                                <input
                                    type="checkbox"
                                    checked={isChecked}
                                    onChange={() => onToggleSkill?.(skill.name, allNames)}
                                    style={{ marginTop: 2, accentColor: 'var(--accent)', flexShrink: 0 }}
                                />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                        <span style={{ fontWeight: 500, fontSize: 'var(--text-xs)' }}>{skill.name}</span>
                                        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)', marginLeft: 4 }}>{skill.category}</span>
                                    </div>
                                    <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)', marginTop: 2 }}>
                                        {skill.description}
                                    </div>
                                </div>
                            </label>
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}

/**
 * MiniMarkdown — a tiny, dependency-free, XSS-safe markdown renderer.
 *
 * The old panel dumped plan text via dangerouslySetInnerHTML. This renders a
 * safe subset (headings, ordered/unordered lists, **bold**, `code`, paragraphs)
 * as real React nodes — enough to make plans and answers read well without
 * pulling in a markdown library.
 */
import React from 'react';

/** Inline parser for **bold** and `code`. Exported for the activity lane. */
export function renderInline(text: string): React.ReactNode[] {
    const nodes: React.ReactNode[] = [];
    const regex = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let lastIndex = 0;
    let key = 0;
    let m: RegExpExecArray | null;
    while ((m = regex.exec(text)) !== null) {
        if (m.index > lastIndex) nodes.push(text.slice(lastIndex, m.index));
        const tok = m[0];
        if (tok.startsWith('**')) nodes.push(<strong key={key++}>{tok.slice(2, -2)}</strong>);
        else nodes.push(<code key={key++} className="hcode-md__code">{tok.slice(1, -1)}</code>);
        lastIndex = m.index + tok.length;
    }
    if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
    return nodes;
}

interface Props {
    text: string;
    className?: string;
}

export default function MiniMarkdown({ text, className }: Props) {
    const lines = text.split('\n');
    const blocks: React.ReactNode[] = [];
    let list: { ordered: boolean; items: string[] } | null = null;
    let key = 0;

    const flushList = () => {
        if (!list) return;
        const items = list.items.map((it, i) => <li key={i}>{renderInline(it)}</li>);
        blocks.push(
            list.ordered
                ? <ol key={key++} className="hcode-md__ol">{items}</ol>
                : <ul key={key++} className="hcode-md__ul">{items}</ul>
        );
        list = null;
    };

    for (const raw of lines) {
        const line = raw.trimEnd();
        if (!line.trim()) { flushList(); continue; }

        const heading = /^(#{1,3})\s+(.*)$/.exec(line);
        if (heading) {
            flushList();
            const txt = renderInline(heading[2]);
            const lvl = heading[1].length;
            blocks.push(
                lvl === 1 ? <h3 key={key++} className="hcode-md__h">{txt}</h3>
                : lvl === 2 ? <h4 key={key++} className="hcode-md__h">{txt}</h4>
                : <h5 key={key++} className="hcode-md__h">{txt}</h5>
            );
            continue;
        }

        const ordered = /^(\d+)\.\s+(.*)$/.exec(line);
        if (ordered) {
            if (!list || !list.ordered) { flushList(); list = { ordered: true, items: [] }; }
            list.items.push(ordered[2]);
            continue;
        }

        const bullet = /^[-*]\s+(.*)$/.exec(line);
        if (bullet) {
            if (!list || list.ordered) { flushList(); list = { ordered: false, items: [] }; }
            list.items.push(bullet[1]);
            continue;
        }

        flushList();
        blocks.push(<p key={key++} className="hcode-md__p">{renderInline(line)}</p>);
    }
    flushList();

    return <div className={`hcode-md${className ? ' ' + className : ''}`}>{blocks}</div>;
}

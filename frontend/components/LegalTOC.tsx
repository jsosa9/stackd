'use client';

import { useEffect, useId, useState } from 'react';

export interface TOCSection {
  id: string;
  label: string;
}

const ITEM_HEIGHT = 40;

export default function LegalTOC({ sections }: { sections: TOCSection[] }) {
  const [activeId, setActiveId] = useState(sections[0]?.id ?? '');
  const [mobileOpen, setMobileOpen] = useState(false);
  const listId = useId();

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveId(entry.target.id);
            break;
          }
        }
      },
      { rootMargin: '-10% 0px -65% 0px' },
    );
    sections.forEach(({ id }) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [sections]);

  // Close on Escape
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMobileOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileOpen]);

  const linkList = (onClick?: () => void) =>
    sections.map(({ id, label }) => {
      const active = activeId === id;
      return (
        <a
          key={id}
          href={`#${id}`}
          aria-current={active ? 'location' : undefined}
          onClick={onClick}
          style={{
            display: 'block',
            height: ITEM_HEIGHT,
            lineHeight: `${ITEM_HEIGHT}px`,
            paddingLeft: 12,
            paddingRight: 12,
            fontSize: 13,
            color: active ? '#C8A97A' : 'rgba(26,22,18,0.6)',
            textDecoration: 'none',
            borderLeft: `2px solid ${active ? '#C8A97A' : 'rgba(26,22,18,0.1)'}`,
            fontWeight: active ? 600 : 400,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            transition: 'color 0.15s, border-color 0.15s',
            background: active ? 'rgba(200,169,122,0.06)' : 'transparent',
          }}
        >
          {label}
        </a>
      );
    });

  return (
    <>
      {/* ── Desktop sidebar — shows all items, no scroll cap ── */}
      <nav
        aria-label="Table of contents"
        className="legal-toc-desktop"
        style={{
          position: 'sticky',
          top: 80,
          width: 200,
          flexShrink: 0,
          alignSelf: 'flex-start',
        }}
      >
        <p
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'rgba(26,22,18,0.35)',
            margin: '0 0 10px',
          }}
        >
          Contents
        </p>
        <div>{linkList()}</div>
      </nav>

      {/* ── Mobile: fixed floating pill at bottom-right ── */}
      <div className="legal-toc-mobile" style={{ position: 'fixed', bottom: 24, right: 20, zIndex: 60 }}>
        {/* Backdrop — closes list when tapping outside */}
        {mobileOpen && (
          <div
            aria-hidden="true"
            onClick={() => setMobileOpen(false)}
            style={{ position: 'fixed', inset: 0, zIndex: -1 }}
          />
        )}

        {/* Expanded list — floats above the pill */}
        {mobileOpen && (
          <div
            id={listId}
            role="list"
            style={{
              position: 'absolute',
              bottom: 'calc(100% + 10px)',
              right: 0,
              width: 230,
              background: '#FAF7F2',
              border: '1px solid rgba(26,22,18,0.1)',
              borderRadius: 12,
              boxShadow: '0 8px 32px rgba(26,22,18,0.12)',
              overflow: 'hidden',
              padding: '6px 0',
            }}
          >
            <p
              style={{
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: 'rgba(26,22,18,0.3)',
                margin: '8px 12px 6px',
              }}
            >
              Contents
            </p>
            {linkList(() => setMobileOpen(false))}
          </div>
        )}

        {/* Pill toggle button */}
        <button
          type="button"
          aria-expanded={mobileOpen}
          aria-controls={listId}
          aria-label="Table of contents"
          onClick={() => setMobileOpen((o) => !o)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '10px 16px',
            background: '#1A1612',
            color: '#FAF7F2',
            border: 'none',
            borderRadius: 999,
            fontSize: 13,
            fontWeight: 500,
            cursor: 'pointer',
            boxShadow: '0 4px 16px rgba(26,22,18,0.25)',
            font: 'inherit',
          }}
        >
          <span aria-hidden="true" style={{ fontSize: 14, lineHeight: 1 }}>☰</span>
          Contents
          <span
            aria-hidden="true"
            style={{
              fontSize: 10,
              transform: mobileOpen ? 'rotate(180deg)' : 'rotate(0deg)',
              transition: 'transform 0.2s',
              display: 'inline-block',
            }}
          >
            ▾
          </span>
        </button>
      </div>

      {/* ── Responsive visibility ── */}
      <style>{`
        .legal-toc-desktop { display: block; }
        .legal-toc-mobile  { display: none; }
        @media (max-width: 720px) {
          .legal-toc-desktop { display: none !important; }
          .legal-toc-mobile  { display: block; }
        }
      `}</style>
    </>
  );
}

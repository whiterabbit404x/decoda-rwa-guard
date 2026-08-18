'use client';

import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

/**
 * Small, self-contained dialog/drawer primitive for Screen 11 Governance Guard
 * (Policy Impact details, Change Log, anomaly details, approvals, critical-change
 * confirmation). Deliberately minimal — no new UI framework. Keyboard accessible:
 * Escape closes, focus moves into the dialog on open, backdrop click closes, and
 * it is announced with role="dialog" + aria-modal.
 */
export function GovernanceDialog({
  open,
  title,
  onClose,
  children,
  footer,
  maxWidth = 680,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  maxWidth?: number;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    // Move focus into the dialog for keyboard and screen-reader users.
    panelRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(1, 4, 9, 0.72)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        zIndex: 1000,
        padding: '4vh 1rem 6vh',
        overflowY: 'auto',
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        style={{
          width: '100%',
          maxWidth,
          background: '#0d1117',
          border: '1px solid #30363d',
          borderRadius: 12,
          boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
          outline: 'none',
        }}
      >
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '1rem',
            padding: '1rem 1.25rem',
            borderBottom: '1px solid #21262d',
          }}
        >
          <h2 style={{ margin: 0, fontSize: '1.05rem' }}>{title}</h2>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={onClose}
            style={{
              background: 'none',
              border: '1px solid #30363d',
              borderRadius: 8,
              color: '#8b949e',
              cursor: 'pointer',
              fontSize: '1.1rem',
              lineHeight: 1,
              padding: '0.3rem 0.6rem',
            }}
          >
            ×
          </button>
        </header>
        <div style={{ padding: '1.1rem 1.25rem', maxHeight: '68vh', overflowY: 'auto' }}>{children}</div>
        {footer ? (
          <footer
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: '0.5rem',
              padding: '0.85rem 1.25rem',
              borderTop: '1px solid #21262d',
            }}
          >
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}

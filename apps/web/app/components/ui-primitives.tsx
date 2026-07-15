'use client';

import Link from 'next/link';
import {
  ReactNode,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FocusEvent as ReactFocusEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { createPortal } from 'react-dom';

/* ── Surface card ─────────────────────────────────────────────── */
export function SurfaceCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <article className={`dataCard sharedSurfaceCard ${className}`.trim()}>{children}</article>;
}

/* ── Metric tile ──────────────────────────────────────────────── */
export function MetricTile({ label, value, meta }: { label: string; value: ReactNode; meta?: ReactNode }) {
  return (
    <article className="metricCard sharedMetricTile">
      <p className="metricLabel">{label}</p>
      <p className="metricValue">{value}</p>
      {meta ? <p className="metricMeta">{meta}</p> : null}
    </article>
  );
}

/* ── Status pill ──────────────────────────────────────────────── */
export type PillVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'default';

function pillClass(variant: PillVariant): string {
  if (variant === 'default') return 'ruleChip sharedStatusPill';
  return `ruleChip sharedStatusPill pill-${variant}`;
}

export function StatusPill({ label, variant = 'default' }: { label: string; variant?: PillVariant }) {
  return <span className={pillClass(variant)}>{label}</span>;
}

export function statusVariantFromSeverity(severity: string): PillVariant {
  switch (severity.toLowerCase()) {
    case 'critical': case 'high':   return 'danger';
    case 'medium':                   return 'warning';
    case 'low':   case 'resolved':  return 'success';
    case 'info':                     return 'info';
    default:                         return 'neutral';
  }
}

export function statusVariantFromStatus(status: string): PillVariant {
  switch (status.toLowerCase()) {
    case 'live':    case 'healthy':  case 'active':   case 'succeeded': return 'success';
    case 'degraded': case 'stale':  case 'warning':  case 'pending':   return 'warning';
    case 'offline': case 'failed':  case 'critical':                    return 'danger';
    case 'investigating': case 'in_progress':                           return 'info';
    default:                                                            return 'neutral';
  }
}

/* ── Table shell ──────────────────────────────────────────────── */
export function TableShell({ headers, children, compact = false }: { headers: string[]; children: ReactNode; compact?: boolean }) {
  return (
    <div className={`tableWrap sharedTableShell${compact ? ' tableCompact' : ''}`}>
      <table>
        <thead>
          <tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/* ── Empty state ──────────────────────────────────────────────── */
export function EmptyStateBlocker({ title, body, ctaHref, ctaLabel, ctaOnClick, ctaDisabled }: { title: string; body: string; ctaHref?: string; ctaLabel?: string; ctaOnClick?: () => void; ctaDisabled?: boolean }) {
  return (
    <div className="emptyStatePanel sharedEmptyStateBlocker">
      <h4>{title}</h4>
      <p className="muted">{body}</p>
      {ctaOnClick && ctaLabel ? (
        <button type="button" className="btn btn-secondary" style={{ marginTop: '0.75rem' }} onClick={ctaOnClick} disabled={ctaDisabled}>{ctaLabel}</button>
      ) : ctaHref && ctaLabel ? (
        <Link href={ctaHref} prefetch={false} className="btn btn-secondary" style={{ marginTop: '0.75rem' }}>{ctaLabel}</Link>
      ) : null}
    </div>
  );
}

/* ── Tab strip ────────────────────────────────────────────────── */
export function TabStrip({ tabs, active, onChange }: { tabs: Array<{ key: string; label: string }>; active: string; onChange: (key: string) => void }) {
  return (
    <div className="buttonRow sharedTabStrip" role="tablist" aria-label="Views">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          role="tab"
          aria-selected={active === tab.key}
          className={active === tab.key ? 'activeTab' : ''}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/* ── CTA panel ────────────────────────────────────────────────── */
export function CtaPanel({ title, children }: { title: string; children: ReactNode }) {
  return <article className="dataCard sharedCtaPanel"><p className="sectionEyebrow">{title}</p>{children}</article>;
}

/* ── Buttons ──────────────────────────────────────────────────── */
export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export function Button({
  children,
  variant = 'secondary',
  disabled = false,
  onClick,
  type = 'button',
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  disabled?: boolean;
  onClick?: () => void;
  type?: 'button' | 'submit' | 'reset';
}) {
  return (
    <button
      type={type}
      className={`btn btn-${variant}`}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function LinkButton({ href, children, variant = 'secondary' }: { href: string; children: ReactNode; variant?: ButtonVariant }) {
  return (
    <Link href={href} prefetch={false} className={`btn btn-${variant}`}>
      {children}
    </Link>
  );
}

/* ── Select (accessible custom listbox) ───────────────────────────
 * Renders the opened option menu inside the application (portal to
 * <body>) instead of the native OS <select> popup, so the dropdown
 * follows the Decoda dark theme instead of a bright native popup.
 * Values are always strings — callers convert to/from their own types
 * (e.g. numeric chain IDs) at the boundary. Semantic theme tokens keep
 * it correct under future Light / Dark / System themes. */
export type SelectOption = { value: string; label: string; detail?: string; disabled?: boolean };

type SelectMenuPos = { left: number; top: number; width: number; maxHeight: number; dropUp: boolean };

export function Select({
  value,
  onValueChange,
  options,
  id,
  name,
  placeholder = 'Select an option',
  disabled = false,
  required = false,
  error = false,
  ariaLabel,
  ariaLabelledBy,
  className = '',
  testId,
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  id?: string;
  name?: string;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  error?: boolean;
  ariaLabel?: string;
  ariaLabelledBy?: string;
  className?: string;
  testId?: string;
}) {
  const reactId = useId();
  const baseId = id ?? `dcsel-${reactId}`;
  const listId = `${baseId}-list`;

  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [pos, setPos] = useState<SelectMenuPos | null>(null);

  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  const typeahead = useRef<{ buffer: string; at: number }>({ buffer: '', at: 0 });

  const selectedIndex = useMemo(() => options.findIndex((o) => o.value === value), [options, value]);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined;

  // Portal is client-only: avoids SSR/hydration mismatch and keeps the
  // menu's positioning styles applied via the CSSOM (CSP-safe).
  useEffect(() => { setMounted(true); }, []);

  const enabledFrom = useCallback((from: number, dir: 1 | -1): number => {
    const n = options.length;
    for (let step = 0; step < n; step++) {
      const i = from + step * dir;
      if (i < 0 || i >= n) break;
      if (!options[i].disabled) return i;
    }
    const fallback = options.findIndex((o) => !o.disabled);
    return fallback;
  }, [options]);

  const computePosition = useCallback((): SelectMenuPos | null => {
    const el = triggerRef.current;
    if (!el || typeof window === 'undefined') return null;
    const rect = el.getBoundingClientRect();
    const gap = 6;
    const spaceBelow = window.innerHeight - rect.bottom - gap;
    const spaceAbove = rect.top - gap;
    const desired = Math.min(288, options.length * 42 + 12);
    const dropUp = spaceBelow < Math.min(desired, 176) && spaceAbove > spaceBelow;
    const maxHeight = Math.max(120, Math.min(desired, dropUp ? spaceAbove : spaceBelow));
    return { left: rect.left, width: rect.width, top: dropUp ? rect.top : rect.bottom, maxHeight, dropUp };
  }, [options.length]);

  const closeMenu = useCallback((focusTrigger: boolean) => {
    setOpen(false);
    setActiveIndex(-1);
    if (focusTrigger) triggerRef.current?.focus();
  }, []);

  const openMenu = useCallback((initialIndex?: number) => {
    if (disabled) return;
    setPos(computePosition());
    setActiveIndex(initialIndex ?? (selectedIndex >= 0 ? selectedIndex : enabledFrom(0, 1)));
    setOpen(true);
  }, [disabled, computePosition, selectedIndex, enabledFrom]);

  const commit = useCallback((index: number) => {
    const opt = options[index];
    if (!opt || opt.disabled) return;
    onValueChange(opt.value);
    closeMenu(true);
  }, [options, onValueChange, closeMenu]);

  // Reposition while open (scroll/resize) and close on outside pointer.
  useEffect(() => {
    if (!open) return;
    const reposition = () => setPos(computePosition());
    const onPointerDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t) || listRef.current?.contains(t)) return;
      closeMenu(false);
    };
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    document.addEventListener('mousedown', onPointerDown);
    return () => {
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
      document.removeEventListener('mousedown', onPointerDown);
    };
  }, [open, computePosition, closeMenu]);

  // Keep the active option scrolled into view.
  useEffect(() => {
    if (!open || activeIndex < 0) return;
    listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [open, activeIndex]);

  const onKeyDown = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (!open) {
      switch (e.key) {
        case 'ArrowDown': case 'ArrowUp': case 'Enter': case ' ': case 'Spacebar':
          e.preventDefault(); openMenu(); return;
        case 'Home':
          e.preventDefault(); openMenu(enabledFrom(0, 1)); return;
        case 'End':
          e.preventDefault(); openMenu(enabledFrom(options.length - 1, -1)); return;
        default: break;
      }
      if (e.key.length === 1 && /\S/.test(e.key)) { e.preventDefault(); openMenu(); handleTypeahead(e.key); }
      return;
    }
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault(); setActiveIndex((i) => enabledFrom(Math.min(options.length - 1, (i < 0 ? -1 : i) + 1), 1)); break;
      case 'ArrowUp':
        e.preventDefault(); setActiveIndex((i) => enabledFrom(Math.max(0, (i < 0 ? options.length : i) - 1), -1)); break;
      case 'Home':
        e.preventDefault(); setActiveIndex(enabledFrom(0, 1)); break;
      case 'End':
        e.preventDefault(); setActiveIndex(enabledFrom(options.length - 1, -1)); break;
      case 'Enter': case ' ': case 'Spacebar':
        e.preventDefault(); if (activeIndex >= 0) commit(activeIndex); break;
      case 'Escape':
        e.preventDefault(); closeMenu(true); break;
      case 'Tab':
        closeMenu(false); break;
      default:
        if (e.key.length === 1 && /\S/.test(e.key)) { e.preventDefault(); handleTypeahead(e.key); }
        break;
    }
  };

  function handleTypeahead(ch: string) {
    const now = Date.now();
    const ta = typeahead.current;
    ta.buffer = now - ta.at > 700 ? ch : ta.buffer + ch;
    ta.at = now;
    const q = ta.buffer.toLowerCase();
    const found = options.findIndex((o) => !o.disabled && o.label.toLowerCase().startsWith(q));
    if (found >= 0) setActiveIndex(found);
  }

  const onBlur = (e: ReactFocusEvent<HTMLButtonElement>) => {
    const next = e.relatedTarget as Node | null;
    if (next && rootRef.current?.contains(next)) return;
    if (open) closeMenu(false);
  };

  const menu = open && mounted && pos ? createPortal(
    <ul
      ref={listRef}
      id={listId}
      role="listbox"
      aria-labelledby={ariaLabelledBy}
      aria-label={ariaLabelledBy ? undefined : ariaLabel}
      className={`dcSelectMenu${pos.dropUp ? ' dcSelectMenu--up' : ''}`}
      style={{
        position: 'fixed',
        left: pos.left,
        width: pos.width,
        maxHeight: pos.maxHeight,
        ...(pos.dropUp ? { bottom: window.innerHeight - pos.top } : { top: pos.top }),
      }}
    >
      {options.map((opt, i) => {
        const isSelected = opt.value === value;
        const isActive = i === activeIndex;
        return (
          <li
            key={opt.value}
            id={`${baseId}-opt-${i}`}
            data-index={i}
            role="option"
            aria-selected={isSelected}
            aria-disabled={opt.disabled || undefined}
            className="dcSelectOption"
            data-active={isActive || undefined}
            data-selected={isSelected || undefined}
            data-disabled={opt.disabled || undefined}
            onMouseEnter={() => { if (!opt.disabled) setActiveIndex(i); }}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => commit(i)}
          >
            <span className="dcSelectOptionLabel">{opt.label}</span>
            {opt.detail ? <span className="dcSelectOptionDetail">{opt.detail}</span> : null}
            <span className="dcSelectCheck" aria-hidden="true">{isSelected ? '✓' : ''}</span>
          </li>
        );
      })}
    </ul>,
    document.body,
  ) : null;

  return (
    <div className={`dcSelect ${className}`.trim()} ref={rootRef}>
      <button
        type="button"
        id={baseId}
        ref={triggerRef}
        className="dcSelectTrigger"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && activeIndex >= 0 ? `${baseId}-opt-${activeIndex}` : undefined}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-required={required || undefined}
        aria-invalid={error || undefined}
        aria-disabled={disabled || undefined}
        data-error={error || undefined}
        data-placeholder={selected ? undefined : true}
        data-testid={testId}
        disabled={disabled}
        onClick={() => (open ? closeMenu(false) : openMenu())}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
      >
        <span className="dcSelectValue">{selected ? selected.label : placeholder}</span>
        <span className="dcSelectArrow" aria-hidden="true">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </span>
      </button>
      {name ? <input type="hidden" name={name} value={value} /> : null}
      {menu}
    </div>
  );
}

/* ── Step rail (onboarding) ───────────────────────────────────── */
export function StepRail({ steps }: { steps: Array<{ key: string; title: string; detail: string; complete: boolean; source?: string; href: string; cta: string }> }) {
  return (
    <div className="stack compactStack">
      {steps.map((step) => (
        <SurfaceCard key={step.key}>
          <div className="listHeader">
            <div>
              <h3>{step.complete ? '✓' : '○'} {step.title}</h3>
              <p className="muted">{step.detail}</p>
            </div>
            {step.source ? <StatusPill label={step.source} variant="info" /> : null}
          </div>
          <Link href={step.href} prefetch={false}>{step.complete ? 'Review' : step.cta}</Link>
        </SurfaceCard>
      ))}
    </div>
  );
}

/* ── Runtime banner (inline variant for other uses) ──────────── */
export function RuntimeBanner({ title, detail }: { title: string; detail: string }) {
  return <div className="statusLine statusLine-warning"><strong>{title}</strong> {detail}</div>;
}

/* ── Aliases for backwards compatibility ──────────────────────── */
export const MetricCard  = MetricTile;
export const DataTable   = TableShell;
export const ActionPanel = CtaPanel;

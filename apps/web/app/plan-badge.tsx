'use client';

import { useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { usePlanStatus } from './plan-status-context';
import {
  USAGE_ROWS,
  isRestrictedLifecycle,
  planBadgeLabel,
  planBadgeTone,
  recommendOnlyNote,
  usageLabel,
  usageRatio,
} from './plan-status';
import { Select } from './components/ui-primitives';
import { FEEDBACK_TYPE_OPTIONS, FEEDBACK_SECRET_WARNING } from './plan-feedback';

/**
 * The plan chip in the existing app shell header.
 *
 * Deliberately small: one chip that opens one panel. No page-level banner, no
 * pricing modal, no second upgrade button per screen. It renders nothing at all
 * when the plan could not be read, because a placeholder chip would state a plan
 * the backend never confirmed.
 */
export default function PlanBadge() {
  const { plan, refresh } = usePlanStatus();
  const [open, setOpen] = useState(false);
  const label = planBadgeLabel(plan);

  if (!label) {
    return null;
  }

  const tone = planBadgeTone(plan);

  return (
    <div className="planBadgeWrap">
      <button
        type="button"
        className={`planBadge pill pill-${tone}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next) {
            void refresh();
          }
        }}
      >
        {label}
      </button>
      {open ? <PlanPanel onClose={() => setOpen(false)} /> : null}
    </div>
  );
}

function PlanPanel({ onClose }: { onClose: () => void }) {
  const { plan, refresh } = usePlanStatus();
  const usage = plan?.usage ?? null;
  const restricted = isRestrictedLifecycle(plan);
  const recommendOnly = recommendOnlyNote(plan);

  return (
    <div className="planPanel" role="dialog" aria-label="Plan and usage">
      <div className="planPanelHeader">
        <span className="sectionEyebrow">{plan?.plan_label ?? 'Plan'}</span>
        <button type="button" className="planPanelClose" onClick={onClose} aria-label="Close plan panel">
          ×
        </button>
      </div>

      {plan?.organization?.name ? <p className="planPanelOrg">{plan.organization.name}</p> : null}

      {restricted ? (
        <p className="planPanelNotice">
          {plan?.lifecycle_state === 'SUSPENDED'
            ? 'This organization is suspended. Existing records remain available; contact Decoda to reactivate it.'
            : 'Your evaluation window has closed. Existing assets, alerts, incidents, and evidence remain '
              + 'available; upgrade to Scale to resume adding monitoring coverage.'}
        </p>
      ) : null}

      <p className="sectionEyebrow planPanelSectionLabel">Usage</p>
      <ul className="planUsageList">
        {USAGE_ROWS.map((row) => {
          const entry = usage?.[row.key] ?? null;
          const ratio = usageRatio(entry);
          return (
            <li key={row.key} className="planUsageRow">
              <span className="planUsageLabel">{row.label}</span>
              <span className="planUsageValue">{usageLabel(entry)}</span>
              {ratio === null ? null : (
                <span className="planUsageMeter" aria-hidden="true">
                  <span className="planUsageMeterFill" style={{ width: `${Math.round(ratio * 100)}%` }} />
                </span>
              )}
            </li>
          );
        })}
      </ul>

      {recommendOnly ? <p className="planPanelNote">{recommendOnly}</p> : null}

      <FeedbackForm onSubmitted={() => void refresh()} />
    </div>
  );
}

function FeedbackForm({ onSubmitted }: { onSubmitted: () => void }) {
  const { authHeaders, csrfReady } = usePilotAuth();
  const [open, setOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState<string>('usability');
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function submit() {
    setStatus('sending');
    setErrorMessage(null);
    try {
      const response = await fetch('/api/account/feedback', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          feedback_type: feedbackType,
          message,
          context: { page: typeof window === 'undefined' ? null : window.location.pathname },
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      if (!response.ok) {
        const detail = payload.detail;
        const detailMessage =
          detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string'
            ? String((detail as Record<string, unknown>).message)
            : typeof detail === 'string'
              ? detail
              : 'Feedback could not be submitted.';
        setStatus('error');
        setErrorMessage(detailMessage);
        return;
      }
      setStatus('sent');
      setMessage('');
      onSubmitted();
    } catch {
      setStatus('error');
      setErrorMessage('Feedback could not be submitted.');
    }
  }

  if (!open) {
    return (
      <button type="button" className="planFeedbackTrigger" onClick={() => setOpen(true)}>
        Give feedback
      </button>
    );
  }

  return (
    <div className="planFeedback">
      {/* Shared in-app listbox rather than a native dropdown: Chrome hands the
          native option popup to the OS, which composites the control's
          translucent surface over white and left the dark theme's light option
          text unreadable. The shared Select draws its menu inside the app from
          the same theme tokens, so every row keeps product contrast. */}
      <label className="planFeedbackLabel" id="plan-feedback-type-label" htmlFor="plan-feedback-type">
        Feedback type
      </label>
      <Select
        id="plan-feedback-type"
        testId="plan-feedback-type"
        className="planFeedbackSelect"
        ariaLabelledBy="plan-feedback-type-label"
        value={feedbackType}
        onValueChange={(value) => setFeedbackType(value)}
        options={FEEDBACK_TYPE_OPTIONS.map((option) => ({ value: option.value, label: option.label }))}
      />

      <label className="planFeedbackLabel" htmlFor="plan-feedback-message">
        What happened?
      </label>
      <textarea
        id="plan-feedback-message"
        className="planFeedbackInput"
        rows={4}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
      />
      <p className="planFeedbackHint">{FEEDBACK_SECRET_WARNING}</p>

      {status === 'sent' ? <p className="planFeedbackOk">Thank you — your feedback was recorded.</p> : null}
      {status === 'error' && errorMessage ? <p className="planFeedbackError">{errorMessage}</p> : null}

      <div className="planFeedbackActions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!csrfReady || status === 'sending' || message.trim().length === 0}
          onClick={() => void submit()}
        >
          {status === 'sending' ? 'Sending…' : 'Send feedback'}
        </button>
        <button type="button" className="btn" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

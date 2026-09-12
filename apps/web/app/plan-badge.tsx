'use client';

import { useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { usePlanStatus } from './plan-status-context';
import {
  PILOT_EVALUATION_ACCESS_NOTE,
  type PlanEvaluation,
  USAGE_ROWS,
  planBadgeLabel,
  planBadgeTone,
  recommendOnlyNote,
  restrictedPlanState,
  usageLabel,
  usageRatio,
} from './plan-status';
import { Select } from './components/ui-primitives';
import PilotFeedbackDialog, { type FeedbackMode } from './pilot-feedback-dialog';
import {
  DETAILED_FEEDBACK_CTA,
  END_OF_PILOT_CTA,
  FEEDBACK_SECRET_WARNING,
  FEEDBACK_SEVERITY_OPTIONS,
  FEEDBACK_TYPE_OPTIONS,
  currentFeedbackContext,
  shouldShowEndOfPilotCta,
} from './plan-feedback';

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
  const restricted = restrictedPlanState(plan);
  const recommendOnly = recommendOnlyNote(plan);
  // Stated only while the evaluation is RUNNING. Once it ends, the restricted
  // notice above is the accurate thing to say, and repeating "your evaluation
  // includes these workflows" underneath it would contradict it.
  const evaluationAccess = plan?.lifecycle_state === 'ACTIVE_PILOT' ? PILOT_EVALUATION_ACCESS_NOTE : null;

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
        <div className="planPanelNotice">
          <p className="planPanelNoticeTitle">{restricted.title}</p>
          <p className="planPanelNoticeBody">{restricted.body}</p>
          {restricted.ctaHref && restricted.ctaLabel ? (
            <a className="btn btn-primary planPanelNoticeCta" href={restricted.ctaHref}>
              {restricted.ctaLabel}
            </a>
          ) : null}
        </div>
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

      {evaluationAccess ? <p className="planPanelNote">{evaluationAccess}</p> : null}
      {recommendOnly ? <p className="planPanelNote">{recommendOnly}</p> : null}

      <FeedbackForm onSubmitted={() => void refresh()} evaluation={plan?.evaluation ?? null} />
    </div>
  );
}

function FeedbackForm({
  onSubmitted,
  evaluation,
}: {
  onSubmitted: () => void;
  evaluation: PlanEvaluation | null;
}) {
  const { authHeaders, csrfReady } = usePilotAuth();
  const [open, setOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState<string>('usability');
  const [severity, setSeverity] = useState<string>('');
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Which structured form is open, if any. The dropdown itself stays compact:
  // the deeper forms are a modal over the page, not more rows in this panel.
  const [dialogMode, setDialogMode] = useState<FeedbackMode | null>(null);
  const endOfPilot = shouldShowEndOfPilotCta(evaluation);

  async function submit() {
    setStatus('sending');
    setErrorMessage(null);
    try {
      const response = await fetch('/api/account/feedback', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          feedback_mode: 'quick',
          feedback_type: feedbackType,
          // '' means "not stated". The backend stores NULL for it rather than a
          // default, so the founder console never shows a priority nobody chose.
          severity,
          message,
          // Page, plus the incident id when the customer is on an incident —
          // never typed by hand, and re-verified against their own tenant
          // server-side before it is stored.
          context: currentFeedbackContext(),
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

  // The two structured forms are reachable whether or not the quick form is
  // expanded, and they render the SAME modal — so the dropdown never grows a
  // second copy of a long form.
  const secondaryActions = (
    <div className="planFeedbackSecondary">
      <button
        type="button"
        className="planFeedbackTrigger"
        data-testid="plan-feedback-detailed-cta"
        onClick={() => setDialogMode('detailed')}
      >
        {DETAILED_FEEDBACK_CTA}
      </button>
      {endOfPilot ? (
        <button
          type="button"
          className="planFeedbackTrigger"
          data-testid="plan-feedback-review-cta"
          onClick={() => setDialogMode('end_of_pilot')}
        >
          {END_OF_PILOT_CTA}
        </button>
      ) : null}
    </div>
  );

  const dialog = dialogMode ? (
    <PilotFeedbackDialog
      mode={dialogMode}
      onClose={() => setDialogMode(null)}
      onSubmitted={onSubmitted}
    />
  ) : null;

  if (!open) {
    return (
      <>
        <button type="button" className="planFeedbackTrigger" onClick={() => setOpen(true)}>
          Give feedback
        </button>
        {secondaryActions}
        {dialog}
      </>
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

      {/* Optional by design: '' posts as "not stated" so the quick form stays a
          two-field form for anyone who just wants to report something. */}
      <label className="planFeedbackLabel" id="plan-feedback-severity-label" htmlFor="plan-feedback-severity">
        Severity (optional)
      </label>
      <Select
        id="plan-feedback-severity"
        testId="plan-feedback-severity"
        className="planFeedbackSelect"
        ariaLabelledBy="plan-feedback-severity-label"
        value={severity}
        onValueChange={(value) => setSeverity(value)}
        options={FEEDBACK_SEVERITY_OPTIONS.map((option) => ({ value: option.value, label: option.label }))}
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

      {secondaryActions}
      {dialog}
    </div>
  );
}

'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

import { Select } from './components/ui-primitives';
import { usePilotAuth } from './pilot-auth-context';
import {
  CONTINUE_INTENT_OPTIONS,
  DETAILED_FEEDBACK_CTA,
  DETAILED_FEEDBACK_FIELDS,
  DETAILED_FEEDBACK_HEADING,
  DETAILED_FEEDBACK_INTRO,
  END_OF_PILOT_FIELDS,
  END_OF_PILOT_HEADING,
  END_OF_PILOT_INTRO,
  FEEDBACK_AREA_OPTIONS,
  FEEDBACK_IMPORTANCE_OPTIONS,
  FEEDBACK_SECRET_WARNING,
  PRODUCTION_BLOCKER_OPTIONS,
  currentFeedbackContext,
} from './plan-feedback';

export type FeedbackMode = 'detailed' | 'end_of_pilot';

/**
 * The structured Pilot feedback form.
 *
 * A modal over the existing `.modalOverlay` / `.modalCard` surface — the pattern
 * this app already uses — rather than a new page, so the plan dropdown stays
 * compact and the customer does not lose the screen they were on. The page they
 * WERE on is the one contextual fact worth keeping, and it is captured
 * automatically below rather than typed.
 *
 * Two modes share one component because they are one submission to one table at
 * two depths. Nothing here is required except what `DETAILED_FEEDBACK_FIELDS`
 * marks required: a half-filled answer is worth more than a refused one.
 */
export default function PilotFeedbackDialog({
  mode,
  onClose,
  onSubmitted,
}: {
  mode: FeedbackMode;
  onClose: () => void;
  onSubmitted?: () => void;
}) {
  const { authHeaders, csrfReady } = usePilotAuth();
  const isReview = mode === 'end_of_pilot';
  const fields = useMemo(() => (isReview ? END_OF_PILOT_FIELDS : DETAILED_FEEDBACK_FIELDS), [isReview]);

  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [feedbackType, setFeedbackType] = useState<string>('other');
  const [severity, setSeverity] = useState<string>('medium');
  const [productionBlocker, setProductionBlocker] = useState<string>('');
  const [continueIntent, setContinueIntent] = useState<string>('');
  const [message, setMessage] = useState('');
  const [contactPermission, setContactPermission] = useState(false);
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showRequired, setShowRequired] = useState(false);
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose();
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const missingRequired = fields
    .filter((field) => field.required)
    .filter((field) => (answers[field.name] ?? '').trim().length === 0)
    .map((field) => field.name);

  // The end-of-Pilot review has no required narrative, so its own free-text
  // "anything else" answer is what makes the submission non-empty. Sending an
  // entirely blank review would record a row that says nothing.
  const anythingAnswered =
    message.trim().length > 0 ||
    fields.some((field) => (answers[field.name] ?? '').trim().length > 0);

  async function submit() {
    if (missingRequired.length > 0) {
      setShowRequired(true);
      return;
    }
    setStatus('sending');
    setErrorMessage(null);
    try {
      const response = await fetch('/api/account/feedback', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          feedback_mode: mode,
          feedback_type: feedbackType,
          // `message` is the row's one required free-text column. For the
          // detailed form the primary answer doubles as it, so the console's
          // summary column is never blank for a row that does have answers.
          message: message.trim() || answers.goal_or_task || answers.where_decoda_helped || '',
          severity: isReview ? '' : severity,
          production_blocker: productionBlocker,
          continue_intent: isReview ? continueIntent : '',
          contact_permission: contactPermission,
          ...Object.fromEntries(fields.map((field) => [field.name, answers[field.name] ?? ''])),
          context: currentFeedbackContext(),
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      if (!response.ok) {
        const detail = payload.detail;
        setStatus('error');
        setErrorMessage(
          detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string'
            ? String((detail as Record<string, unknown>).message)
            : typeof detail === 'string'
              ? detail
              : 'Feedback could not be submitted.',
        );
        return;
      }
      setStatus('sent');
      onSubmitted?.();
    } catch {
      setStatus('error');
      setErrorMessage('Feedback could not be submitted.');
    }
  }

  return (
    <div className="modalOverlay pilotFeedbackOverlay" onClick={onClose}>
      <div
        className="modalCard pilotFeedbackCard"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pilot-feedback-heading"
        data-testid="pilot-feedback-dialog"
        data-mode={mode}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="pilotFeedbackHeader">
          <div>
            <h2 id="pilot-feedback-heading" className="pilotFeedbackTitle" tabIndex={-1} ref={headingRef}>
              {isReview ? END_OF_PILOT_HEADING : DETAILED_FEEDBACK_HEADING}
            </h2>
            <p className="pilotFeedbackIntro">{isReview ? END_OF_PILOT_INTRO : DETAILED_FEEDBACK_INTRO}</p>
          </div>
          <button type="button" className="planPanelClose" onClick={onClose} aria-label="Close feedback form">
            ×
          </button>
        </div>

        {status === 'sent' ? (
          <div className="pilotFeedbackDone">
            <p className="planFeedbackOk">Thank you — your feedback was recorded.</p>
            <button type="button" className="btn btn-primary" onClick={onClose}>
              Close
            </button>
          </div>
        ) : (
          <>
            {fields.map((field) => {
              const inputId = `pilot-feedback-${field.name}`;
              const invalid = showRequired && missingRequired.includes(field.name);
              return (
                <div className="pilotFeedbackField" key={field.name}>
                  <label className="planFeedbackLabel" htmlFor={inputId}>
                    {field.label}
                    {field.required ? <span className="pilotFeedbackRequired"> (required)</span> : null}
                  </label>
                  {field.hint ? <p className="pilotFeedbackHint">{field.hint}</p> : null}
                  <textarea
                    id={inputId}
                    data-testid={inputId}
                    className="planFeedbackInput"
                    rows={3}
                    aria-required={field.required || undefined}
                    aria-invalid={invalid || undefined}
                    value={answers[field.name] ?? ''}
                    onChange={(event) =>
                      setAnswers((prev) => ({ ...prev, [field.name]: event.target.value }))
                    }
                  />
                  {invalid ? (
                    <p className="planFeedbackError" role="alert">
                      This answer is required.
                    </p>
                  ) : null}
                </div>
              );
            })}

            {isReview ? null : (
              <div className="pilotFeedbackField">
                <label className="planFeedbackLabel" id="pilot-feedback-severity-label" htmlFor="pilot-feedback-severity">
                  How important is this problem?
                </label>
                <Select
                  id="pilot-feedback-severity"
                  testId="pilot-feedback-severity"
                  className="planFeedbackSelect"
                  ariaLabelledBy="pilot-feedback-severity-label"
                  value={severity}
                  onValueChange={setSeverity}
                  options={FEEDBACK_IMPORTANCE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
                />
              </div>
            )}

            <div className="pilotFeedbackField">
              <label className="planFeedbackLabel" id="pilot-feedback-area-label" htmlFor="pilot-feedback-area">
                Feedback area
              </label>
              <Select
                id="pilot-feedback-area"
                testId="pilot-feedback-area"
                className="planFeedbackSelect"
                ariaLabelledBy="pilot-feedback-area-label"
                value={feedbackType}
                onValueChange={setFeedbackType}
                options={FEEDBACK_AREA_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              />
            </div>

            <div className="pilotFeedbackField">
              <label className="planFeedbackLabel" id="pilot-feedback-blocker-label" htmlFor="pilot-feedback-blocker">
                {isReview
                  ? 'Would this prevent production deployment?'
                  : 'Would this prevent you from using Decoda in production?'}
              </label>
              <Select
                id="pilot-feedback-blocker"
                testId="pilot-feedback-blocker"
                className="planFeedbackSelect"
                ariaLabelledBy="pilot-feedback-blocker-label"
                value={productionBlocker}
                onValueChange={setProductionBlocker}
                options={PRODUCTION_BLOCKER_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              />
            </div>

            {isReview ? (
              <div className="pilotFeedbackField">
                <label className="planFeedbackLabel" id="pilot-feedback-continue-label" htmlFor="pilot-feedback-continue">
                  Would you continue using Decoda?
                </label>
                <Select
                  id="pilot-feedback-continue"
                  testId="pilot-feedback-continue"
                  className="planFeedbackSelect"
                  ariaLabelledBy="pilot-feedback-continue-label"
                  value={continueIntent}
                  onValueChange={setContinueIntent}
                  options={CONTINUE_INTENT_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
                />
              </div>
            ) : null}

            <div className="pilotFeedbackField">
              <label className="planFeedbackLabel" htmlFor="pilot-feedback-message">
                {isReview ? 'Anything else we should improve?' : 'Anything else?'}
              </label>
              <textarea
                id="pilot-feedback-message"
                data-testid="pilot-feedback-message"
                className="planFeedbackInput"
                rows={3}
                value={message}
                onChange={(event) => setMessage(event.target.value)}
              />
            </div>

            <label className="pilotFeedbackCheckbox" htmlFor="pilot-feedback-contact">
              <input
                id="pilot-feedback-contact"
                data-testid="pilot-feedback-contact"
                type="checkbox"
                checked={contactPermission}
                onChange={(event) => setContactPermission(event.target.checked)}
              />
              <span>Can we contact you about this feedback?</span>
            </label>

            <p className="planFeedbackHint">{FEEDBACK_SECRET_WARNING}</p>
            {status === 'error' && errorMessage ? (
              <p className="planFeedbackError" role="alert">
                {errorMessage}
              </p>
            ) : null}

            <div className="planFeedbackActions">
              <button
                type="button"
                className="btn btn-primary"
                data-testid="pilot-feedback-submit"
                disabled={!csrfReady || status === 'sending' || !anythingAnswered}
                onClick={() => void submit()}
              >
                {status === 'sending' ? 'Sending…' : isReview ? 'Send review' : 'Send feedback'}
              </button>
              <button type="button" className="btn" onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export { DETAILED_FEEDBACK_CTA };

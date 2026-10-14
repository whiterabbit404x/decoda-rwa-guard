'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import {
  DUPLICATE_BODY,
  SECRET_WARNING,
  SUBMITTED_BODY,
  SUBMITTED_HEADLINE,
  USE_CASE_SUGGESTIONS,
} from 'app/pilot-request-copy';
import { HomeIcon } from '../components/home/home-icons';
import site from '../components/home/site.module.css';
import pages from '../components/home/pages.module.css';

type SubmitResult = { duplicate: boolean };

function errorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object') {
    const detail = (payload as Record<string, unknown>).detail;
    if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string') {
      return String((detail as Record<string, unknown>).message);
    }
    if (typeof detail === 'string') {
      return detail;
    }
  }
  if (status === 429) {
    return 'Too many requests from this address. Please try again shortly.';
  }
  return `Your request could not be submitted (HTTP ${status}).`;
}

export default function RequestPilotClient() {
  const [email, setEmail] = useState('');
  const [companyName, setCompanyName] = useState('');
  const [role, setRole] = useState('');
  const [companyWebsite, setCompanyWebsite] = useState('');
  const [useCase, setUseCase] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const confirmationRef = useRef<HTMLHeadingElement>(null);

  // The confirmation replaces the form; move focus to it so keyboard and
  // screen-reader users land on the outcome instead of on a removed button.
  useEffect(() => {
    if (result) {
      confirmationRef.current?.focus();
    }
  }, [result]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch('/api/pilot-requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        cache: 'no-store',
        body: JSON.stringify({
          email,
          company_name: companyName,
          role,
          company_website: companyWebsite,
          use_case: useCase,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(errorMessage(payload, response.status));
        return;
      }
      setResult({ duplicate: Boolean((payload as Record<string, unknown>).duplicate) });
    } catch {
      setError('Your request could not be submitted. Please check your connection and try again.');
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <section className={`${pages.formCard} ${pages.confirmation}`} aria-live="polite">
        <span className={pages.confirmationIcon} aria-hidden="true">
          <HomeIcon name="checkCircle" size={22} />
        </span>
        <h2 ref={confirmationRef} tabIndex={-1}>
          {result.duplicate ? 'Request already under review' : SUBMITTED_HEADLINE}
        </h2>
        <p className={pages.formLede}>{result.duplicate ? DUPLICATE_BODY : SUBMITTED_BODY}</p>
        <p className={pages.footnote}>
          Already approved and have an invitation? Open the link in your invitation email to activate your
          evaluation.
        </p>
        <Link href="/" className={`${site.btnSecondary} ${pages.confirmationBack}`} prefetch={false}>
          Back to Decoda RWA Guard
        </Link>
      </section>
    );
  }

  return (
    <section className={pages.formCard} aria-labelledby="request-pilot-form-title">
      <h2 id="request-pilot-form-title">Tell us about your team</h2>
      <p className={pages.formLede}>Every field is required unless it is marked optional.</p>

      {error ? (
        <div className={pages.alert} role="alert">
          {error}
        </div>
      ) : null}

      <form onSubmit={handleSubmit} noValidate>
        <div className={pages.field}>
          <label className={pages.label} htmlFor="rp-email">
            Work email
          </label>
          <input
            id="rp-email"
            className={pages.input}
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
            placeholder="you@company.com"
            maxLength={254}
            required
          />
        </div>

        <div className={pages.fieldRow}>
          <div className={pages.field}>
            <label className={pages.label} htmlFor="rp-company">
              Company name
            </label>
            <input
              id="rp-company"
              className={pages.input}
              type="text"
              value={companyName}
              onChange={(event) => setCompanyName(event.target.value)}
              autoComplete="organization"
              placeholder="Company or fund name"
              maxLength={200}
              required
            />
          </div>

          <div className={pages.field}>
            <label className={pages.label} htmlFor="rp-role">
              Role / title
            </label>
            <input
              id="rp-role"
              className={pages.input}
              type="text"
              value={role}
              onChange={(event) => setRole(event.target.value)}
              autoComplete="organization-title"
              placeholder="Head of Security, CTO…"
              maxLength={120}
              required
            />
          </div>
        </div>

        <div className={pages.field}>
          <label className={pages.label} htmlFor="rp-website">
            Company website <span className={pages.optional}>(optional)</span>
          </label>
          <input
            id="rp-website"
            className={pages.input}
            type="text"
            value={companyWebsite}
            onChange={(event) => setCompanyWebsite(event.target.value)}
            autoComplete="url"
            placeholder="company.com"
            maxLength={300}
          />
        </div>

        <div className={pages.field}>
          <label className={pages.label} htmlFor="rp-use-case">
            Intended use case
          </label>
          <textarea
            id="rp-use-case"
            className={`${pages.input} ${pages.textarea}`}
            value={useCase}
            onChange={(event) => setUseCase(event.target.value)}
            placeholder="What do you want to monitor, and what would a successful evaluation prove?"
            rows={4}
            maxLength={2000}
            aria-describedby="pilot-secret-warning"
            required
          />
          <div className={pages.suggestions}>
            <span className={pages.suggestionsLabel}>Common evaluations:</span>
            {USE_CASE_SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className={pages.suggestion}
                onClick={() => setUseCase((current) => (current.trim() ? current : suggestion))}
              >
                {suggestion}
              </button>
            ))}
          </div>
          {/* Mirrors the backend rule. A submission that looks like it carries a
              key is REFUSED rather than stored and redacted, so there is nothing
              to leak from the review console later. */}
          <p id="pilot-secret-warning" className={pages.warning}>
            {SECRET_WARNING}
          </p>
        </div>

        <button type="submit" className={`${site.btnPrimary} ${site.btnLg} ${pages.submit}`} disabled={submitting}>
          {submitting ? 'Submitting…' : 'Submit Pilot request'}
        </button>
      </form>

      <p className={pages.footnote}>
        Already have a Decoda account?{' '}
        <Link href="/sign-in" prefetch={false}>
          Sign in
        </Link>
        .
      </p>
    </section>
  );
}

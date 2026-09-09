'use client';

import Link from 'next/link';
import { useState } from 'react';

import {
  DUPLICATE_BODY,
  REVIEW_NOTICE,
  SECRET_WARNING,
  SUBMITTED_BODY,
  SUBMITTED_HEADLINE,
  USE_CASE_SUGGESTIONS,
} from 'app/pilot-request-copy';

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
      <main id="request-pilot-main" className="pilotRequestPage">
        <section className="pilotRequestCard" aria-live="polite">
          <p className="mktSectionLabel">PILOT EVALUATION</p>
          <h1 className="pilotRequestTitle">
            {result.duplicate ? 'Request already under review' : SUBMITTED_HEADLINE}
          </h1>
          <p className="pilotRequestLede">{result.duplicate ? DUPLICATE_BODY : SUBMITTED_BODY}</p>
          <p className="pilotRequestFootnote">
            Already approved and have an invitation? Open the link in your invitation email to
            activate your evaluation.
          </p>
          <Link href="/" className="pilotRequestSecondary" prefetch={false}>Back to decodasecurity.com</Link>
        </section>
      </main>
    );
  }

  return (
    <main id="request-pilot-main" className="pilotRequestPage">
      <section className="pilotRequestCard">
        <p className="mktSectionLabel">PILOT EVALUATION</p>
        <h1 className="pilotRequestTitle">Request a Pilot evaluation</h1>
        <p className="pilotRequestLede">{REVIEW_NOTICE}</p>

        {error ? (
          <div className="pilotRequestAlert" role="alert">{error}</div>
        ) : null}

        <form onSubmit={handleSubmit} noValidate>
          <div className="suFormGroup">
            <label className="suLabel" htmlFor="rp-email">WORK EMAIL</label>
            <input
              id="rp-email"
              className="suInput"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              placeholder="you@company.com"
              maxLength={254}
              required
            />
          </div>

          <div className="suFormGroup">
            <label className="suLabel" htmlFor="rp-company">COMPANY NAME</label>
            <input
              id="rp-company"
              className="suInput"
              type="text"
              value={companyName}
              onChange={(event) => setCompanyName(event.target.value)}
              autoComplete="organization"
              placeholder="Company or fund name"
              maxLength={200}
              required
            />
          </div>

          <div className="suFormGroup">
            <label className="suLabel" htmlFor="rp-role">ROLE / TITLE</label>
            <input
              id="rp-role"
              className="suInput"
              type="text"
              value={role}
              onChange={(event) => setRole(event.target.value)}
              autoComplete="organization-title"
              placeholder="Head of Security, CTO, Operations Lead…"
              maxLength={120}
              required
            />
          </div>

          <div className="suFormGroup">
            <label className="suLabel" htmlFor="rp-website">
              COMPANY WEBSITE <span className="pilotRequestOptional">(optional)</span>
            </label>
            <input
              id="rp-website"
              className="suInput"
              type="text"
              value={companyWebsite}
              onChange={(event) => setCompanyWebsite(event.target.value)}
              autoComplete="url"
              placeholder="company.com"
              maxLength={300}
            />
          </div>

          <div className="suFormGroup">
            <label className="suLabel" htmlFor="rp-use-case">INTENDED USE CASE</label>
            <textarea
              id="rp-use-case"
              className="suInput pilotRequestTextarea"
              value={useCase}
              onChange={(event) => setUseCase(event.target.value)}
              placeholder="What do you want to monitor, and what would a successful evaluation prove?"
              rows={4}
              maxLength={2000}
              required
            />
            <div className="pilotRequestSuggestions">
              <span className="pilotRequestSuggestionsLabel">Common evaluations:</span>
              {USE_CASE_SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  className="pilotRequestSuggestion"
                  onClick={() => setUseCase((current) => (current.trim() ? current : suggestion))}
                >
                  {suggestion}
                </button>
              ))}
            </div>
            {/* Mirrors the backend rule. A submission that looks like it carries a
                key is REFUSED rather than stored and redacted, so there is nothing
                to leak from the review console later. */}
            <p className="pilotRequestWarning">{SECRET_WARNING}</p>
          </div>

          <button type="submit" className="suSubmitBtn" disabled={submitting}>
            {submitting ? 'Submitting…' : 'Submit Pilot request'}
          </button>
        </form>

        <p className="pilotRequestFootnote">
          Already have a Decoda account? <Link href="/sign-in" prefetch={false}>Sign in</Link>.
        </p>
      </section>
    </main>
  );
}

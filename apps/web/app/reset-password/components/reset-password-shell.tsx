import Link from 'next/link';

import { CheckIcon, LockIcon, ArrowIcon, ShieldIcon } from './reset-icons';

/**
 * Page frame for account recovery: brand panel on the left, the active step's card
 * on the right. Reuses the sign-in visual language (`si*` tokens extended with `rp*`)
 * so recovery does not look like a different product.
 *
 * The left panel is supporting context, not content: at tablet width and below it
 * collapses away entirely and the card becomes the whole page.
 */

export type ResetStepId = 1 | 2 | 3;

const STEPS: { id: ResetStepId; label: string }[] = [
  { id: 1, label: 'Request link' },
  { id: 2, label: 'Check email' },
  { id: 3, label: 'Create new password' },
];

function StepIndicator({ activeStep }: { activeStep: ResetStepId }) {
  return (
    <ol className="rpSteps" aria-label={`Account recovery, step ${activeStep} of ${STEPS.length}`}>
      {STEPS.map((step) => {
        const state = step.id === activeStep ? 'active' : step.id < activeStep ? 'done' : 'todo';
        return (
          <li
            key={step.id}
            className={`rpStep rpStep--${state}`}
            aria-current={state === 'active' ? 'step' : undefined}
          >
            <span className="rpStepMarker" aria-hidden="true">
              {state === 'done' ? <CheckIcon size={14} /> : step.id}
            </span>
            <span className="rpStepLabel">{step.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

const ASSURANCES = [
  {
    icon: <CheckIcon size={18} />,
    title: 'Single-use reset links',
    body: 'Every reset link works once and expires on its own.',
  },
  {
    icon: <LockIcon size={18} />,
    title: 'Passwords are never stored in plain text',
    body: 'Your new password is hashed before it is saved.',
  },
  {
    icon: <ArrowIcon size={18} />,
    title: "You'll sign in again afterwards",
    body: 'Resetting signs out existing sessions on your account.',
  },
];

export default function ResetPasswordShell({
  activeStep,
  children,
}: {
  activeStep: ResetStepId;
  children: React.ReactNode;
}) {
  return (
    <div className="siPage">
      <div className="siWrapper">
        <div className="siContainer">
          <main className="siOuter rpOuter">
            <section className="siBrand" aria-label="Decoda Security account recovery">
              <div className="siBrandInner">
                <header className="siLogoHeader">
                  <div className="siLogoIcon" aria-hidden="true">
                    <svg width="34" height="34" viewBox="0 0 34 34" fill="none">
                      <path d="M17 3L4 8.5v9c0 7.5 5.5 13 13 15 7.5-2 13-7.5 13-15v-9L17 3z" fill="rgba(59,130,246,0.18)" stroke="#3b82f6" strokeWidth="1.8" strokeLinejoin="round" />
                      <path d="M12 17l3.5 3.5L23 13" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </div>
                  <div className="siLogoText">
                    <span className="siLogoName">DECODA</span>
                    <span className="siLogoSub">SECURITY</span>
                  </div>
                </header>

                <span className="siBadge">Account recovery</span>

                <h1 className="siHeadline">Secure account recovery</h1>

                <p className="siLede">
                  You&apos;re a few steps away from getting back into Decoda Security. Reset links are
                  sent to the account&apos;s own email address and can only be used once.
                </p>

                <ul className="rpAssurances" role="list">
                  {ASSURANCES.map((assurance) => (
                    <li key={assurance.title} className="rpAssurance">
                      <span className="rpAssuranceIcon" aria-hidden="true">{assurance.icon}</span>
                      <span>
                        <span className="rpAssuranceTitle">{assurance.title}</span>
                        <span className="rpAssuranceBody">{assurance.body}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="siBrandFooter">
                <p className="siTrustLine">
                  <span style={{ display: 'flex', color: '#4ade80' }}><ShieldIcon size={15} /></span>
                  Secure. Reliable. Purpose-built for Real-World Assets.
                </p>
              </div>
            </section>

            <section className="siFormPanel" aria-labelledby="reset-step-heading">
              <StepIndicator activeStep={activeStep} />
              {children}
            </section>
          </main>
        </div>
      </div>

      {/* Mirrors the sign-in footer, including its static year, so the two auth
          pages stay consistent and neither risks a server/client date mismatch. */}
      <footer className="siFooter">
        <p className="siFooterCopy">&copy; 2026 Decoda Security. All rights reserved.</p>
        <nav className="siFooterLinks" aria-label="Account links">
          <Link href="/sign-in" className="siFooterLink" prefetch={false}>Sign in</Link>
          <span className="siFooterSep" aria-hidden="true">|</span>
          <Link href="/sign-up" className="siFooterLink" prefetch={false}>Create an account</Link>
        </nav>
      </footer>
    </div>
  );
}

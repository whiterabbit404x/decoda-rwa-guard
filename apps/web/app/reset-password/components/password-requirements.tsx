'use client';

import { describePasswordStrength, evaluatePasswordRequirements } from '../../password-policy';

/**
 * The policy checklist and strength meter under the new-password field.
 *
 * Both read from `app/password-policy.ts`, which mirrors what the API enforces, so the
 * list can never promise the user a rule the backend does not apply — or hide one it does.
 */
export default function PasswordRequirements({ password, describedById }: { password: string; describedById: string }) {
  const requirements = evaluatePasswordRequirements(password);
  const strength = describePasswordStrength(password);

  return (
    <div className="rpPolicy" id={describedById}>
      <div className="rpStrengthHeader">
        <span className="rpStrengthLabel">Password strength</span>
        <span className={`rpStrengthValue rpStrengthValue--${strength.label.toLowerCase().replace(' ', '-')}`}>
          {strength.label}
        </span>
      </div>

      <div
        className="rpStrengthTrack"
        role="meter"
        aria-valuenow={strength.score}
        aria-valuemin={0}
        aria-valuemax={strength.total}
        aria-valuetext={`${strength.label}: ${strength.score} of ${strength.total} requirements met`}
        aria-label="Password strength"
      >
        {Array.from({ length: strength.total }, (_, index) => (
          <span
            key={index}
            className={`rpStrengthSegment${index < strength.score ? ` rpStrengthSegment--${strength.label.toLowerCase().replace(' ', '-')}` : ''}`}
          />
        ))}
      </div>

      <ul className="rpRequirements" role="list">
        {requirements.map((requirement) => (
          <li
            key={requirement.id}
            className={`rpRequirement${requirement.met ? ' rpRequirement--met' : ''}`}
          >
            <span className="rpRequirementMark" aria-hidden="true">
              {requirement.met ? (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M3 6.3l2 2L9 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : null}
            </span>
            {/* The met/unmet state is carried in text, not only in colour and a tick. */}
            <span>
              {requirement.label}
              <span className="rpVisuallyHidden">{requirement.met ? ' — met' : ' — not met yet'}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

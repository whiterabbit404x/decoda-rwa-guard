'use client';

import { useCallback, useState } from 'react';

import {
  buildDeploymentVersionFields,
  type DeploymentVersionField,
  type DeploymentVersionInput,
} from './deployment-version';

function CopyShaButton({ fullValue, label }: { fullValue: string; label: string }) {
  const [copied, setCopied] = useState(false);

  const onCopy = useCallback(() => {
    const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined;
    if (!clipboard?.writeText) {
      return;
    }

    void clipboard
      .writeText(fullValue)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        // Clipboard can be blocked by permissions; the full SHA is still exposed via
        // the button's title/aria-label tooltip, so we fail quietly.
      });
  }, [fullValue]);

  return (
    <button
      type="button"
      className="deploymentVersionCopy"
      onClick={onCopy}
      // Tooltip + accessible name both carry the complete SHA (requirement: copy
      // button OR tooltip with the full SHA — we provide both).
      title={`Full commit SHA: ${fullValue}`}
      aria-label={`Copy full ${label} SHA ${fullValue}`}
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

function DeploymentVersionRow({ field }: { field: DeploymentVersionField }) {
  return (
    <div
      className="deploymentVersionRow"
      data-field={field.key}
      data-available={field.available ? 'true' : 'false'}
    >
      <dt className="deploymentVersionLabel">{field.label}</dt>
      <dd className="deploymentVersionValue">
        <span
          className={
            field.available
              ? 'deploymentVersionText'
              : 'deploymentVersionText deploymentVersionText-unknown'
          }
          // For an available commit, expose the full SHA on hover of the value too.
          title={field.kind === 'commit' && field.fullValue ? field.fullValue : undefined}
        >
          {field.display}
        </span>
        {field.kind === 'commit' && field.fullValue ? (
          <CopyShaButton fullValue={field.fullValue} label={field.label} />
        ) : null}
      </dd>
    </div>
  );
}

// Always-rendered deployment identity block. It lives near the top of System Health
// and is intentionally OUTSIDE the backend-health conditional so the web (frontend)
// commit stays visible even when the API health endpoint is unreachable. Every field
// renders; a missing value reads as the truthful unavailable message.
export function DeploymentVersionSection(props: DeploymentVersionInput) {
  const fields = buildDeploymentVersionFields(props);

  return (
    <section className="dataCard deploymentVersionCard" aria-label="Deployment Version">
      <div className="deploymentVersionHeader">
        <p className="sectionEyebrow">Deployment identity</p>
        <h2 className="deploymentVersionTitle">Deployment Version</h2>
        <p className="deploymentVersionSubtitle">
          Exact web (Vercel) and API (Railway) commits serving this page. Compare them to confirm
          production is running the expected build. A missing value is shown truthfully as unknown,
          never hidden.
        </p>
      </div>
      <dl className="deploymentVersionGrid">
        {fields.map((field) => (
          <DeploymentVersionRow key={field.key} field={field} />
        ))}
      </dl>
    </section>
  );
}

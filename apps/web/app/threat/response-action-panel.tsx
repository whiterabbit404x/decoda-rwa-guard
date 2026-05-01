import type { ReactNode } from 'react';

type Props = { capabilityLabels?: string[]; message?: string; children?: ReactNode };

export default function ResponseActionPanel({ capabilityLabels, message, children }: Props) {
  if (children) return <section aria-label="Response Actions">{children}</section>;
  return (
    <article className="dataCard" aria-label="Response Actions">
      <p className="sectionEyebrow">Response actions</p>
      <h3>Action capability and workflow state</h3>
      <div className="chipRow">
        {(capabilityLabels ?? ['Simulation only', 'Manual recommendation', 'Live executable', 'Approval required', 'Executed', 'Failed', 'Rolled back']).map((label) => <span className="ruleChip" key={label}>{label}</span>)}
      </div>
      {message ? <p className="tableMeta">{message}</p> : null}
    </article>
  );
}

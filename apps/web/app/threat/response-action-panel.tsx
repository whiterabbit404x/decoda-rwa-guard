type InternalCapabilityState =
  | 'simulation_only'
  | 'manual_recommendation'
  | 'live_executable'
  | 'approval_required'
  | 'executed'
  | 'failed'
  | 'rolled_back';

type ResponseActionItem = {
  id: string;
  actionLabel: string;
  capabilityState: InternalCapabilityState;
  detail?: string;
};

import type { ReactNode } from 'react';

type Props = { actions?: ResponseActionItem[]; children?: ReactNode };

const CAPABILITY_LABELS: Record<InternalCapabilityState, string> = {
  simulation_only: 'Simulation only',
  manual_recommendation: 'Manual recommendation',
  live_executable: 'Live executable',
  approval_required: 'Approval required',
  executed: 'Executed',
  failed: 'Failed',
  rolled_back: 'Rolled back',
};

export default function ResponseActionPanel({ actions = [], children }: Props) {
  return (
    <section aria-label="Response Actions" className="sidebarMetaCard">
      <h3>Response actions</h3>
      {actions.length === 0 ? children ?? (
        <p className="tableMeta">No response actions are available yet.</p>
      ) : (
        <ul className="statusMatrix">
          {actions.map((action) => (
            <li key={action.id} className="statusMatrixRow">
              <span>{action.actionLabel}</span>
              <span className="statusMatrixMeta">
                <strong>{CAPABILITY_LABELS[action.capabilityState]}</strong>
                {action.detail ? <small>{action.detail}</small> : null}
              </span>
            </li>
          ))}
        </ul>
      )}
      {children && actions.length > 0 ? children : null}
    </section>
  );
}

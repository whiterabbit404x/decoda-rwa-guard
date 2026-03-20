'use client';

import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

type DemoPanelProps = {
  apiUrl: string;
};

type ScenarioMap = typeof transferScenarios;
type TransferScenarioKey = keyof ScenarioMap;
type ResidencyScenarioKey = keyof typeof residencyScenarios;
type GovernanceScenarioKey = keyof typeof governanceScenarios;

const transferScenarios = {
  compliant_transfer_approved: {
    label: 'Compliant transfer approved',
    body: {
      asset_id: 'USTB-2026',
      sender_wallet: '0xaaa0000000000000000000000000000000000101',
      receiver_wallet: '0xbbb0000000000000000000000000000000000202',
      amount: 250000,
      sender_kyc_status: 'verified',
      receiver_kyc_status: 'verified',
      sender_jurisdiction: 'US',
      receiver_jurisdiction: 'GB',
      sender_sanctions_flag: false,
      receiver_sanctions_flag: false,
      sender_accreditation_status: 'approved',
      receiver_accreditation_status: 'approved',
      asset_transfer_policy: {
        restricted_jurisdictions: ['IR', 'KP'],
        review_jurisdictions: ['RU'],
        amount_review_threshold: 500000,
        amount_block_threshold: 1500000,
        requires_accreditation: true,
        allowed_assets: ['USTB-2026', 'USTB-2027']
      },
      wallet_tags: {
        sender: ['treasury-desk', 'allowlisted'],
        receiver: ['qualified-custodian', 'allowlisted']
      }
    }
  },
  blocked_transfer_sanctions: {
    label: 'Blocked by sanctions',
    body: {
      asset_id: 'USTB-2026',
      sender_wallet: '0xsanc00000000000000000000000000000000011',
      receiver_wallet: '0xbbb0000000000000000000000000000000000202',
      amount: 100000,
      sender_kyc_status: 'verified',
      receiver_kyc_status: 'verified',
      sender_jurisdiction: 'US',
      receiver_jurisdiction: 'GB',
      sender_sanctions_flag: true,
      receiver_sanctions_flag: false,
      sender_accreditation_status: 'approved',
      receiver_accreditation_status: 'approved',
      asset_transfer_policy: {
        restricted_jurisdictions: ['IR', 'KP'],
        review_jurisdictions: ['RU'],
        amount_review_threshold: 500000,
        amount_block_threshold: 1500000,
        requires_accreditation: true,
        allowed_assets: ['USTB-2026', 'USTB-2027']
      },
      wallet_tags: {
        sender: ['external-counterparty'],
        receiver: ['allowlisted']
      }
    }
  },
  review_transfer_incomplete_kyc: {
    label: 'Review for incomplete KYC',
    body: {
      asset_id: 'USTB-2026',
      sender_wallet: '0xaaa0000000000000000000000000000000000101',
      receiver_wallet: '0xccc0000000000000000000000000000000000303',
      amount: 420000,
      sender_kyc_status: 'verified',
      receiver_kyc_status: 'incomplete',
      sender_jurisdiction: 'US',
      receiver_jurisdiction: 'GB',
      sender_sanctions_flag: false,
      receiver_sanctions_flag: false,
      sender_accreditation_status: 'approved',
      receiver_accreditation_status: 'approved',
      asset_transfer_policy: {
        restricted_jurisdictions: ['IR', 'KP'],
        review_jurisdictions: ['RU'],
        amount_review_threshold: 500000,
        amount_block_threshold: 1500000,
        requires_accreditation: true,
        allowed_assets: ['USTB-2026', 'USTB-2027']
      },
      wallet_tags: {
        sender: ['allowlisted'],
        receiver: ['new-investor']
      }
    }
  },
  transfer_blocked_asset_paused: {
    label: 'Blocked because asset paused',
    body: {
      asset_id: 'USTB-PAUSED-2026',
      sender_wallet: '0xaaa0000000000000000000000000000000000101',
      receiver_wallet: '0xbbb0000000000000000000000000000000000202',
      amount: 750000,
      sender_kyc_status: 'verified',
      receiver_kyc_status: 'verified',
      sender_jurisdiction: 'US',
      receiver_jurisdiction: 'GB',
      sender_sanctions_flag: false,
      receiver_sanctions_flag: false,
      sender_accreditation_status: 'approved',
      receiver_accreditation_status: 'approved',
      asset_transfer_policy: {
        restricted_jurisdictions: ['IR', 'KP'],
        review_jurisdictions: ['RU'],
        amount_review_threshold: 500000,
        amount_block_threshold: 1500000,
        requires_accreditation: true,
        allowed_assets: ['USTB-PAUSED-2026'],
        asset_status: 'paused'
      },
      wallet_tags: {
        sender: ['allowlisted'],
        receiver: ['allowlisted']
      }
    }
  }
} as const;

const residencyScenarios = {
  allowed_residency: {
    label: 'Allowed residency',
    body: {
      asset_id: 'USTB-2026',
      requested_processing_region: 'us-east',
      asset_home_jurisdiction: 'US',
      approved_regions: ['us-east', 'us-central', 'eu-west'],
      restricted_regions: ['cn-north', 'ru-central', 'ir-gov'],
      sensitivity_level: 'sensitive',
      cloud_environment: 'sovereign-cloud-a'
    }
  },
  denied_residency_restricted_region: {
    label: 'Denied restricted region',
    body: {
      asset_id: 'USTB-2026',
      requested_processing_region: 'cn-north',
      asset_home_jurisdiction: 'US',
      approved_regions: ['us-east', 'us-central', 'eu-west'],
      restricted_regions: ['cn-north', 'ru-central', 'ir-gov'],
      sensitivity_level: 'sovereign',
      cloud_environment: 'commercial-cloud-b'
    }
  }
} as const;

const governanceScenarios = {
  governance_freeze_wallet: {
    label: 'Freeze wallet',
    body: {
      action_type: 'freeze_wallet',
      target_type: 'wallet',
      target_id: '0xddd0000000000000000000000000000000000404',
      actor: 'governance-multisig',
      reason: 'Escalated compliance review after repeated sanctions-adjacent transfers.',
      related_asset_id: 'USTB-2026',
      metadata: { ticket: 'CMP-1042', severity: 'high' }
    }
  },
  governance_pause_asset: {
    label: 'Pause asset',
    body: {
      action_type: 'pause_asset_transfers',
      target_type: 'asset',
      target_id: 'USTB-2026',
      actor: 'governance-multisig',
      reason: 'Pause asset transfers while wrapper thresholds are being recalibrated.',
      related_asset_id: 'USTB-2026',
      metadata: { ticket: 'CMP-1043' }
    }
  },
  governance_allowlist_wallet: {
    label: 'Allowlist wallet',
    body: {
      action_type: 'allowlist_wallet',
      target_type: 'wallet',
      target_id: '0xeee0000000000000000000000000000000000505',
      actor: 'governance-multisig',
      reason: 'Approved new qualified custodian wallet for primary market settlements.',
      related_asset_id: 'USTB-2026',
      metadata: { ticket: 'CMP-1044' }
    }
  }
} as const;

export default function ComplianceDemoPanel({ apiUrl }: DemoPanelProps) {
  const router = useRouter();
  const { isAuthenticated, user, authHeaders } = usePilotAuth();
  const [transferScenario, setTransferScenario] = useState<TransferScenarioKey>('compliant_transfer_approved');
  const [residencyScenario, setResidencyScenario] = useState<ResidencyScenarioKey>('denied_residency_restricted_region');
  const [governanceScenario, setGovernanceScenario] = useState<GovernanceScenarioKey>('governance_freeze_wallet');
  const [transferResult, setTransferResult] = useState<string>('Run a transfer screening scenario to inspect wrapper reasons.');
  const [residencyResult, setResidencyResult] = useState<string>('Run a residency scenario to inspect region controls.');
  const [governanceResult, setGovernanceResult] = useState<string>('Submit a governance action to update policy state.');
  const [loading, setLoading] = useState<'transfer' | 'residency' | 'governance' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const currentTransfer = useMemo(() => transferScenarios[transferScenario], [transferScenario]);
  const currentResidency = useMemo(() => residencyScenarios[residencyScenario], [residencyScenario]);
  const currentGovernance = useMemo(() => governanceScenarios[governanceScenario], [governanceScenario]);

  async function postJson(path: string, body: unknown) {
    const livePrefix = isAuthenticated && user?.current_workspace?.id ? '/pilot' : '';
    const response = await fetch(`${apiUrl}${livePrefix}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    return response.json();
  }

  async function runTransfer() {
    setLoading('transfer');
    setError(null);
    try {
      const result = await postJson('/compliance/screen/transfer', currentTransfer.body);
      setTransferResult(JSON.stringify(result, null, 2));
      window.dispatchEvent(new Event('pilot-history-refresh'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to run transfer screening.');
    } finally {
      setLoading(null);
    }
  }

  async function runResidency() {
    setLoading('residency');
    setError(null);
    try {
      const result = await postJson('/compliance/screen/residency', currentResidency.body);
      setResidencyResult(JSON.stringify(result, null, 2));
      window.dispatchEvent(new Event('pilot-history-refresh'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to run residency screening.');
    } finally {
      setLoading(null);
    }
  }

  async function submitGovernance() {
    setLoading('governance');
    setError(null);
    try {
      const result = await postJson('/compliance/governance/actions', currentGovernance.body);
      setGovernanceResult(JSON.stringify(result, null, 2));
      window.dispatchEvent(new Event('pilot-history-refresh'));
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to submit governance action.');
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="dataCard demoPanel complianceDemoPanel">
      <div className="sectionHeader compact">
        <div>
          <h3>Feature 3 demo interactions</h3>
          <p>Run wrapper screening and governance actions through the API gateway.</p>
        </div>
        <span className="pill">{isAuthenticated && user?.current_workspace ? `Live workspace: ${user.current_workspace.name}` : apiUrl}</span>
      </div>

      <div className="stack compactStack">
        <div>
          <label className="label" htmlFor="feature3-transfer-scenario">Transfer scenario</label>
          <select id="feature3-transfer-scenario" value={transferScenario} onChange={(event) => setTransferScenario(event.target.value as TransferScenarioKey)}>
            {Object.entries(transferScenarios).map(([key, scenario]) => (
              <option key={key} value={key}>{scenario.label}</option>
            ))}
          </select>
          <button type="button" onClick={runTransfer} disabled={loading !== null}>
            {loading === 'transfer' ? 'Running transfer screening…' : 'Run transfer screening'}
          </button>
          <pre>{transferResult}</pre>
        </div>

        <div>
          <label className="label" htmlFor="feature3-residency-scenario">Residency scenario</label>
          <select id="feature3-residency-scenario" value={residencyScenario} onChange={(event) => setResidencyScenario(event.target.value as ResidencyScenarioKey)}>
            {Object.entries(residencyScenarios).map(([key, scenario]) => (
              <option key={key} value={key}>{scenario.label}</option>
            ))}
          </select>
          <button type="button" onClick={runResidency} disabled={loading !== null}>
            {loading === 'residency' ? 'Running residency screening…' : 'Run residency screening'}
          </button>
          <pre>{residencyResult}</pre>
        </div>

        <div>
          <label className="label" htmlFor="feature3-governance-scenario">Governance action</label>
          <select id="feature3-governance-scenario" value={governanceScenario} onChange={(event) => setGovernanceScenario(event.target.value as GovernanceScenarioKey)}>
            {Object.entries(governanceScenarios).map(([key, scenario]) => (
              <option key={key} value={key}>{scenario.label}</option>
            ))}
          </select>
          <button type="button" onClick={submitGovernance} disabled={loading !== null}>
            {loading === 'governance' ? 'Submitting governance action…' : 'Submit governance action'}
          </button>
          <pre>{governanceResult}</pre>
        </div>
      </div>

      {error ? <p className="errorText">{error}</p> : null}
    </div>
  );
}

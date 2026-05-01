import Link from 'next/link';

type ActionItem = { id: string; label: string; disabled?: boolean; reason?: string; onClick?: () => void };
type Props = { capabilities?: string[]; actions?: ActionItem[] };

export default function ResponseActionPanel({ capabilities = [], actions = [] }: Props) {
  return (
    <article className="dataCard" aria-label="Response Actions">
      <p className="sectionEyebrow">Response actions</p>
      <h3>Operational actions</h3>
      <div className="chipRow">{capabilities.map((label) => <span className="ruleChip" key={label}>{label}</span>)}</div>
      <div className="buttonRow">{actions.map((action) => <button key={action.id} type="button" disabled={action.disabled} title={action.reason} onClick={action.onClick}>{action.label}</button>)}</div>
      <div className="buttonRow"><Link href="/alerts" prefetch={false}>Review alerts</Link><Link href="/incidents" prefetch={false}>Open incident queue</Link></div>
    </article>
  );
}

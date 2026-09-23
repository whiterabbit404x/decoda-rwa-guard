'use client';

import { useEffect, useState } from 'react';

import type { ProspectReport } from './external-watchlist-types';
import {
  buildProspectReportSvg,
  prospectReportRows,
  prospectReportText,
  formatTimestamp,
} from './external-watchlist-view';

/**
 * The shareable prospect report.
 *
 * Renders ONLY the fields the backend put in the report — protocol, network,
 * contract, transaction, time, the observed change, the three-part analysis,
 * the evidence verification status and the disclaimer. No Decoda id, score,
 * triage status or provider detail is on this panel, and "Export Screenshot"
 * draws the image from the same report object rather than capturing the page.
 */
export default function ProspectReportPanel({ report, onClose }: { report: ProspectReport; onClose: () => void }) {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function exportScreenshot() {
    setMessage(null);
    const { svg, width, height } = buildProspectReportSvg(report);
    const scale = 2;
    const image = new Image();
    image.decoding = 'async';
    const loaded = new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error('render failed'));
    });
    image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
    try {
      await loaded;
      const canvas = document.createElement('canvas');
      canvas.width = width * scale;
      canvas.height = height * scale;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('no canvas');
      context.scale(scale, scale);
      context.drawImage(image, 0, 0, width, height);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('no image');
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const safeName = report.protocol.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase() || 'protocol';
      link.href = url;
      link.download = `decoda-${safeName}-${(report.transaction ?? 'report').slice(0, 10)}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage('Screenshot exported.');
    } catch {
      setMessage('The screenshot could not be rendered in this browser. Use "Copy text" instead.');
    }
  }

  async function copyText() {
    try {
      await navigator.clipboard.writeText(prospectReportText(report));
      setMessage('Report text copied.');
    } catch {
      setMessage('Copy is not available in this browser.');
    }
  }

  return (
    <div className="modalOverlay ewlReportOverlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <article className="modalCard ewlReport" role="dialog" aria-modal="true" aria-labelledby="ewl-report-heading" data-testid="prospect-report">
        <p className="ewlReportSource">{report.source_label ?? 'External Public Monitoring'}</p>
        <h2 id="ewl-report-heading" className="ewlReportHeading">{report.heading}</h2>
        {report.change_title ? <p className="ewlReportSubheading">{report.change_title}</p> : null}

        <dl className="ewlReportRows">
          {prospectReportRows(report).map((row) => (
            <div key={row.label}>
              <dt>{row.label}</dt>
              <dd className={['Contract', 'Transaction', 'Evidence SHA-256'].includes(row.label) ? 'ewlMono ewlBreak' : undefined}>
                {row.label === 'Transaction' && report.transaction_explorer_url ? (
                  <a href={report.transaction_explorer_url} target="_blank" rel="noopener noreferrer nofollow">{row.value}</a>
                ) : row.label === 'Contract' && report.contract_explorer_url ? (
                  <a href={report.contract_explorer_url} target="_blank" rel="noopener noreferrer nofollow">{row.value}</a>
                ) : row.value}
              </dd>
            </div>
          ))}
        </dl>

        <section className="ewlReportAnalysis">
          <p className="sectionEyebrow">Decoda analysis</p>
          <p><strong>Observed fact.</strong> {report.decoda_analysis.observed_fact ?? '—'}</p>
          <p><strong>Decoda interpretation.</strong> {report.decoda_analysis.decoda_interpretation ?? '—'}</p>
          <p><strong>Operational authorization.</strong> {report.decoda_analysis.operational_authorization ?? '—'}</p>
        </section>

        <p className="ewlReportDisclaimer" data-testid="prospect-report-disclaimer">{report.disclaimer}</p>
        <p className="muted ewlSmall">Prepared by {report.prepared_by ?? 'Decoda Security'} · {formatTimestamp(report.generated_at ?? null)}</p>

        {message ? <p className="statusLine" role="status">{message}</p> : null}
        <div className="ewlDialogActions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>Close</button>
          <button type="button" className="btn btn-secondary" onClick={() => void copyText()}>Copy text</button>
          <button type="button" className="btn btn-primary" data-testid="export-screenshot" onClick={() => void exportScreenshot()}>
            Export Screenshot
          </button>
        </div>
      </article>
    </div>
  );
}

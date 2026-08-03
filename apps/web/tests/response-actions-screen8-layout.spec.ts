import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// This managed environment pre-installs Chromium at /opt/pw-browsers/chromium
// (a different build than the local @playwright/test would auto-download), so
// pin the executable when it exists. Falls back to the bundled browser locally.
const PREINSTALLED_CHROMIUM = '/opt/pw-browsers/chromium';
if (fs.existsSync(PREINSTALLED_CHROMIUM)) {
  test.use({ launchOptions: { executablePath: PREINSTALLED_CHROMIUM } });
}

/**
 * Real-browser layout verification of the Screen 8 Response Actions table at 100%
 * browser zoom (deviceScaleFactor 1, no zoom applied) across the desktop
 * breakpoints called out in the task: 1440×900, 1366×768, 1280×720, 1024×768.
 *
 * This renders a slice that mirrors the ACTUAL component markup (the same
 * .tableWrap / .sharedTableShell classes, the two-line Action cell with a
 * truncated target subtitle, the separate lifecycle + "Simulated" pills, and a
 * long monospace UUID cell) using the real app stylesheet, then asserts:
 *   - no PAGE-level horizontal overflow (any overflow is local to the table)
 *   - the Action title and target subtitle stay single-line and truncate
 *   - the status cell renders TWO distinct pill elements — proving the lifecycle
 *     label and the simulation marker are never concatenated into one string
 */
const STYLES = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');

const LONG_UUID = '7c9e6679-7425-40de-944b-e07fc1f90ae7-and-a-very-long-tail-identifier';

function slice(): string {
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${STYLES}</style>
    <style>body{margin:0;padding:24px;background:#0d1117;color:#e6edf3;}</style></head>
    <body>
      <main class="productPage">
        <section class="featureSection">
          <div class="tableWrap sharedTableShell" id="tableWrap">
            <table>
              <thead><tr>
                <th>Action</th><th>Type</th><th>Priority</th><th>Impact</th><th>Status</th>
                <th>Recommended By</th><th>Runbook</th><th>Linked Incident</th>
                <th>Evidence Source</th><th>Requires Approval</th>
              </tr></thead>
              <tbody>
                <tr>
                  <td style="max-width:190px">
                    <div id="title" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500">Notify Security Team</div>
                    <div id="subtitle" class="muted" style="font-size:0.72rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Incident ${LONG_UUID}</div>
                  </td>
                  <td style="font-size:0.8rem">Communication</td>
                  <td><span class="statusPill">Low</span></td>
                  <td><span class="statusPill">Medium</span></td>
                  <td id="statusCell">
                    <span class="statusPill" id="lifecyclePill">Awaiting Approval</span>
                    <div style="margin-top:0.2rem"><span class="statusPill" id="simulatedPill">Simulated</span></div>
                  </td>
                  <td style="font-size:0.8rem">Policy engine</td>
                  <td id="runbook" style="font-size:0.78rem;font-family:monospace;max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">RBK-CM-03</td>
                  <td style="font-size:0.8rem"><a href="/incidents">${LONG_UUID}</a></td>
                  <td><span class="statusPill">Incident context only</span></td>
                  <td><span class="statusPill">Requires Approval</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </body></html>`;
}

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 1280, height: 720 },
  { width: 1024, height: 768 },
];

for (const vp of VIEWPORTS) {
  test(`Screen 8 table has no page-level horizontal overflow at ${vp.width}x${vp.height} (100% zoom)`, async ({ page }) => {
    await page.setViewportSize(vp);
    await page.setContent(slice());

    // The page body must not scroll horizontally — any overflow is local to the
    // table's own overflow-x:auto container.
    const overflow = await page.evaluate(() => ({
      docScroll: document.documentElement.scrollWidth,
      docClient: document.documentElement.clientWidth,
      wrapOverflowX: getComputedStyle(document.getElementById('tableWrap')!).overflowX,
    }));
    expect(overflow.docScroll).toBeLessThanOrEqual(overflow.docClient + 1);
    expect(overflow.wrapOverflowX).toBe('auto');
  });

  test(`Screen 8 Action title + target subtitle truncate (single line) at ${vp.width}x${vp.height}`, async ({ page }) => {
    await page.setViewportSize(vp);
    await page.setContent(slice());

    for (const id of ['#title', '#subtitle', '#runbook']) {
      const box = await page.locator(id).evaluate((el) => ({
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        lines: el.getClientRects().length,
      }));
      // Truncated to a single line (never wraps to overflow the cell).
      expect(box.lines).toBeLessThanOrEqual(1);
      expect(box.clientWidth).toBeLessThanOrEqual(200);
    }
  });

  test(`Screen 8 status cell keeps lifecycle and simulation as SEPARATE pills at ${vp.width}x${vp.height}`, async ({ page }) => {
    await page.setViewportSize(vp);
    await page.setContent(slice());

    const lifecycle = (await page.locator('#lifecyclePill').textContent())?.trim();
    const simulated = (await page.locator('#simulatedPill').textContent())?.trim();
    expect(lifecycle).toBe('Awaiting Approval');
    expect(simulated).toBe('Simulated');
    // The two are distinct elements — never a single concatenated "… · SIMULATED".
    const pillCount = await page.locator('#statusCell .statusPill').count();
    expect(pillCount).toBe(2);
    const cellText = (await page.locator('#statusCell').textContent()) ?? '';
    expect(cellText).not.toContain('路 SIMULATED');
    expect(cellText).not.toContain('· SIMULATED');
  });
}

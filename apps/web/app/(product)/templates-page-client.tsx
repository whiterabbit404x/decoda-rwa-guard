'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function TemplatesPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [templates, setTemplates] = useState<any[]>([]);
  const [message, setMessage] = useState('');

  async function load() {
    const response = await fetch(`${apiUrl}/templates`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) return;
    setTemplates((await response.json()).templates ?? []);
  }

  useEffect(() => { void load(); }, []);

  async function applyTemplate(templateId: string) {
    const response = await fetch(`${apiUrl}/templates/${templateId}/apply`, { method: 'POST', headers: authHeaders() });
    setMessage(response.ok ? 'Template applied to current workspace.' : 'Unable to apply template.');
  }

  return <main className="productPage"><section className="dataCard"><h1>Templates</h1><p className="muted">Optional onboarding helpers. Templates never replace live operations.</p>{templates.length === 0 ? <p className="muted">No templates available.</p> : templates.map((template) => <div key={template.id}><p>{template.name} · {template.module}</p><button type="button" onClick={() => void applyTemplate(template.id)}>Apply</button></div>)}{message ? <p className="statusLine">{message}</p> : null}</section></main>;
}

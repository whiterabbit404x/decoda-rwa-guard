from pathlib import Path

p = Path(r"apps/web/app/evidence-audit-panel.tsx")
s = p.read_text(encoding="utf-8")

s = s.replace("""<<<<<<< claude/follow-claude-guidelines-cZjO6
    return { label: 'missing', variant: 'neutral' };
  }
  if (raw === 'unavailable' || raw === 'fallback') {
    return { label: 'unavailable', variant: 'warning' };
=======
    return { label: 'Evidence missing', variant: 'neutral' };
  }
  if (raw === 'unavailable' || raw === 'fallback') {
    return { label: 'Evidence unavailable', variant: 'warning' };
>>>>>>> main""", """    return { label: 'Evidence missing', variant: 'neutral' };
  }
  if (raw === 'unavailable' || raw === 'fallback') {
    return { label: 'Evidence unavailable', variant: 'warning' };""")

s = s.replace("""<<<<<<< claude/follow-claude-guidelines-cZjO6
  return { label: 'unknown', variant: 'neutral' };
=======
  return { label: 'Unknown source', variant: 'neutral' };
>>>>>>> main""", """  return { label: 'Unknown source', variant: 'neutral' };""")

s = s.replace("""<<<<<<< claude/follow-claude-guidelines-cZjO6
          <StatusPill label="Incomplete" variant="danger" />
        ) : pkg.export_status === 'partial' ? (
          <StatusPill label="Partial" variant="warning" />
        ) : ready ? (
          <StatusPill label="Ready" variant="success" />
=======
          <StatusPill label="Incomplete proof bundle" variant="danger" />
        ) : pkg.export_status === 'partial' ? (
          <StatusPill label="Partial proof bundle" variant="warning" />
        ) : ready ? (
          <StatusPill label="Proof bundle ready" variant="success" />
>>>>>>> main""", """          <StatusPill label="Incomplete proof bundle" variant="danger" />
        ) : pkg.export_status === 'partial' ? (
          <StatusPill label="Partial proof bundle" variant="warning" />
        ) : ready ? (
          <StatusPill label="Proof bundle ready" variant="success" />""")

p.write_text(s, encoding="utf-8")
print("Done")
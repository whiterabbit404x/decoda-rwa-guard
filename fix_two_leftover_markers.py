from pathlib import Path

# Fix evidence-audit-panel.tsx: remove leftover marker lines, keep the main unavailable_sections block
p = Path(r"apps/web/app/evidence-audit-panel.tsx")
s = p.read_text(encoding="utf-8-sig")

s = s.replace("""<<<<<<< claude/follow-claude-guidelines-cZjO6
=======

      {(pkg.unavailable_sections?.length ?? 0) > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>
            Unavailable Sections
          </p>
          {pkg.unavailable_sections?.map((section) => (
            <div
              key={section}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.2rem', fontSize: '0.75rem' }}
            >
              <span style={{ color: '#f59e0b', fontWeight: 700 }}>!</span>
              <span style={{ color: '#fcd34d' }}>{section}</span>
            </div>
          ))}
        </div>
      )}
>>>>>>> main""", """
      {(pkg.unavailable_sections?.length ?? 0) > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>
            Unavailable Sections
          </p>
          {pkg.unavailable_sections?.map((section) => (
            <div
              key={section}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.2rem', fontSize: '0.75rem' }}
            >
              <span style={{ color: '#f59e0b', fontWeight: 700 }}>!</span>
              <span style={{ color: '#fcd34d' }}>{section}</span>
            </div>
          ))}
        </div>
      )}""")

p.write_text(s, encoding="utf-8")


# Fix test_proof_bundle_export.py: remove empty leftover marker block
p = Path(r"services/api/tests/test_proof_bundle_export.py")
s = p.read_text(encoding="utf-8-sig")

s = s.replace("""<<<<<<< claude/follow-claude-guidelines-cZjO6
=======

>>>>>>> main
""", "\n")

p.write_text(s, encoding="utf-8")

print("Fixed leftover conflict markers")
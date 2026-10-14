import { FINAL_CTA } from './home-data';
import { CtaBand } from './site-ui';

/** Section I — the closing call to action. */
export function FinalCTA() {
  return (
    <CtaBand
      id="final-cta-title"
      title={FINAL_CTA.title}
      body={FINAL_CTA.body}
      primary={FINAL_CTA.primary}
      secondary={FINAL_CTA.secondary}
    />
  );
}

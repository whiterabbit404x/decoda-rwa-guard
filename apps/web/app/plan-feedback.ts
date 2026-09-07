// Feedback vocabulary shared by the badge form and its tests. The values must
// match services/api/app/organizations.FEEDBACK_TYPES exactly — the backend
// rejects anything else, so a drift here would surface as a 400 the user cannot
// act on.
export const FEEDBACK_TYPE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'security', label: 'Security' },
  { value: 'detection_accuracy', label: 'Detection accuracy' },
  { value: 'usability', label: 'Usability' },
  { value: 'missing_feature', label: 'Missing feature' },
  { value: 'integration', label: 'Integration' },
  { value: 'other', label: 'Other' },
];

export const FEEDBACK_SECRET_WARNING =
  'Do not include private keys, credentials, seed phrases, or other secrets.';

/** Normalise lane names, including the legacy strong/weak aliases. */
export const normalizeLaneKey = (value) => {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'trainer' || normalized === 'strong') return 'trainer';
  if (normalized === 'agent' || normalized === 'weak') return 'agent';
  return '';
};

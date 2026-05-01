import { redirect } from 'next/navigation';

type LegacyRouteProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

function serializeSearchParams(searchParams: LegacyRouteProps['searchParams']) {
  const params = new URLSearchParams();
  if (!searchParams) return '';

  for (const [key, value] of Object.entries(searchParams)) {
    if (Array.isArray(value)) {
      value.forEach((entry) => params.append(key, entry));
    } else if (typeof value === 'string') {
      params.set(key, value);
    }
  }

  const query = params.toString();
  return query ? `?${query}` : '';
}

export default function TargetsPage({ searchParams }: LegacyRouteProps) {
  redirect(`/monitoring-sources/targets${serializeSearchParams(searchParams)}`);
}

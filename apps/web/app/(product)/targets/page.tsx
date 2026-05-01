import { redirect } from 'next/navigation';

type LegacySearchParams = Record<string, string | string[] | undefined>;

type LegacyRouteProps = {
  searchParams?: Promise<LegacySearchParams>;
};

function serializeSearchParams(searchParams: LegacySearchParams | undefined) {
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

export default async function TargetsPage({ searchParams }: LegacyRouteProps) {
  const resolvedSearchParams = await searchParams;
  redirect(`/monitoring-sources/targets${serializeSearchParams(resolvedSearchParams)}`);
}

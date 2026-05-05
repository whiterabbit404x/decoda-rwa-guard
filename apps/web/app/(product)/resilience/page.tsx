import { redirect } from 'next/navigation';

export default function ResiliencePageRedirect() {
  redirect('/system-health');
}

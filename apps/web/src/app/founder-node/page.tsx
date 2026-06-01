import { redirect } from 'next/navigation';

/** Legacy URL — everything lives under Builder / Founder Node settings now. */
export default function FounderNodeRedirectPage() {
  redirect('/settings/builder');
}

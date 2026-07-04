import { redirect } from 'next/navigation';

/** Legacy URL — download & pairing hub lives on Founder Den onboarding. */
export default function FounderNodeRedirectPage() {
  redirect('/founder-den?onboard=sovereign#founder-node-download');
}

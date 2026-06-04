import { redirect } from 'next/navigation';

/** Legacy URL — Builder Rewards replaced Airdrop Runway. */
export default function AirdropRedirectPage() {
  redirect('/builder-rewards');
}

import { redirect } from 'next/navigation';

/** Scout voting lives in Trust Center — one destination, one nav item. */
export default function ScoutVotesRedirectPage() {
  redirect('/trust-center?tab=scout-voting');
}

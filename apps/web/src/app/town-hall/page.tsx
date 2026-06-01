import { redirect } from 'next/navigation';

/** Platform announcements are part of the public Feed. */
export default function TownHallRedirectPage() {
  redirect('/feed?view=announcements');
}

import { redirect } from 'next/navigation';

/** Developer docs live inside Founder OS — not a top-level nav destination. */
export default function DevelopersRedirectPage() {
  redirect('/founder-den');
}

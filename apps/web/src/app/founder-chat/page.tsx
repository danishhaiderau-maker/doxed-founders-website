import { redirect } from 'next/navigation';

/** Legacy deep link used by the old Founder Chat launcher. */
export default function FounderChatRedirectPage() {
  redirect('/chat');
}

import { redirect } from 'next/navigation';

export default function SecuritySettingsRedirect() {
  redirect('/account?tab=security');
}

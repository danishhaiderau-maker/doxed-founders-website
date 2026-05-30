'use client';

import { useEffect } from 'react';
import { useSession } from 'next-auth/react';

const PROMPT_KEY = 'dcf-push-prompted';

export function PushNotificationPrompt() {
  const { data: session, status } = useSession();

  useEffect(() => {
    if (status !== 'authenticated' || !session?.user) return;
    if (typeof window === 'undefined' || !('Notification' in window)) return;
    if (Notification.permission !== 'default') return;
    if (localStorage.getItem(PROMPT_KEY)) return;

    const timer = setTimeout(() => {
      const accepted = window.confirm(
        'Enable alerts for Scout Votes, Hot Buys, founder updates, and Raise Room opens?',
      );
      localStorage.setItem(PROMPT_KEY, '1');
      if (accepted) {
        Notification.requestPermission().catch(() => undefined);
      }
    }, 2500);

    return () => clearTimeout(timer);
  }, [status, session?.user]);

  return null;
}

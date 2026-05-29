import type { Metadata } from 'next';
import { AuthProvider } from '@/components/auth-provider';
import { NotificationFlashProvider } from '@/components/notification-flash';
import './globals.css';

export const metadata: Metadata = {
  title: 'Doxxed crypto — The Reputation Layer for Crypto',
  description:
    'Would you send money to a stranger? Discover doxxed founders, paper trade, earn reputation, and find conviction before liquidity.',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    title: 'DoxedFounders',
    statusBarStyle: 'black-translucent',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <NotificationFlashProvider>{children}</NotificationFlashProvider>
        </AuthProvider>
      </body>
    </html>
  );
}

import type { Metadata } from 'next';
import { AuthProvider } from '@/components/auth-provider';
import { NotificationFlashProvider } from '@/components/notification-flash';
import './globals.css';

export const metadata: Metadata = {
  title: 'Doxxed crypto — Build publicly. Earn trust. Launch responsibly.',
  description:
    'Founder reputation network and startup validation platform. Public video presence, build logs, simulated demand — no passport uploads.',
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

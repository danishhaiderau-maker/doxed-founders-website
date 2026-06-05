import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'digital.doxxedcrypto.app',
  appName: 'Doxxed Crypto',
  webDir: 'www',
  server: {
    // Load local compat gate first (avoids white screen on Android 6 / old WebView), then redirect.
    cleartext: false,
    androidScheme: 'https',
  },
  android: {
    allowMixedContent: false,
  },
};

export default config;

import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'digital.doxxedcrypto.app',
  appName: 'Doxxed Crypto',
  webDir: 'www',
  server: {
    url: 'https://doxxedcrypto.digital',
    cleartext: false,
    androidScheme: 'https',
  },
  android: {
    allowMixedContent: false,
  },
};

export default config;

import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'digital.doxxedcrypto.app',
  appName: 'Doxxed Crypto',
  webDir: 'www',
  server: {
    // Phone-first: Discover, rankings, trust, agents, community trading
    url: 'https://doxxedcrypto.digital/discover?app=android',
    cleartext: false,
    androidScheme: 'https',
  },
  android: {
    allowMixedContent: false,
  },
};

export default config;

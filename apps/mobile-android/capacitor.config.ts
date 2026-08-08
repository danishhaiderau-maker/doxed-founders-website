import type { CapacitorConfig } from '@capacitor/cli';

/**
 * Unified Doxxed Crypto mobile shell (complete package v0.5).
 * Local www/ hub → production web. Does NOT bundle Founder Node / Void / Ollama.
 */
const config: CapacitorConfig = {
  appId: 'digital.doxxedcrypto.app',
  appName: 'Doxxed Crypto',
  webDir: 'www',
  server: {
    cleartext: false,
    androidScheme: 'https',
    allowNavigation: [
      'doxxedcrypto.digital',
      '*.doxxedcrypto.digital',
      'www.doxxedcrypto.digital',
    ],
  },
  android: {
    allowMixedContent: false,
  },
};

export default config;

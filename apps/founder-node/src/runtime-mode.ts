export function isEmbeddedRelayMode(
  env: NodeJS.ProcessEnv = process.env,
  argv: readonly string[] = process.argv,
): boolean {
  return (
    env.FOUNDER_NODE_EMBEDDED === '1' ||
    argv.includes('--embedded-founder-ide')
  );
}

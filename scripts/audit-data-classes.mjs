/**
 * P0 static audit — fails CI if public/private data-class registry is non-compliant.
 */
import { runStaticDataClassAudit } from '@dcf/utils';

const audit = runStaticDataClassAudit();

console.log('\n=== Data class audit (P0) ===\n');
for (const f of audit.findings) {
  const tag = f.severity === 'ok' ? 'OK  ' : f.severity === 'warn' ? 'WARN' : 'INFO';
  console.log(`${tag}  ${f.code}: ${f.message}`);
}
console.log(`\nModels documented: ${audit.modelCount}`);
console.log(`Route patterns: ${audit.routeCount}`);
console.log(`Compliant: ${audit.compliant}\n`);

if (!audit.compliant) {
  process.exit(1);
}

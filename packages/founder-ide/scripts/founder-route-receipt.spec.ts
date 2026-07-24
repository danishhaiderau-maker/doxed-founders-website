import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { stripUntrustedFounderRouteReceipts } from '../upstream/overlay/src/vs/workbench/contrib/void/electron-main/llmMessage/founderNativeRouting';

describe('Founder route receipt trust boundary', () => {
  it('removes a provider-authored receipt and its separator', () => {
    assert.equal(
      stripUntrustedFounderRouteReceipts(
        'Founder Auto is online.\n\n---\n**Founder route** | reasoning | google/gemini-3-pro | 1167 ms',
      ),
      'Founder Auto is online.',
    );
  });

  it('removes multiple copied receipt lines without deleting ordinary prose', () => {
    assert.equal(
      stripUntrustedFounderRouteReceipts(
        [
          'The application will show routing evidence below.',
          'Founder route | fast | fake/provider',
          '### **Founder route** | reasoning | fake/model',
          'The Founder router design remains available for inspection.',
        ].join('\n'),
      ),
      [
        'The application will show routing evidence below.',
        'The Founder router design remains available for inspection.',
      ].join('\n'),
    );
  });
});

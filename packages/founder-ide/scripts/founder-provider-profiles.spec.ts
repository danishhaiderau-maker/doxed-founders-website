import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  FOUNDER_PROVIDER_PROFILE_LIMIT,
  FOUNDER_PROVIDER_PROFILES_KEY,
  headersWithoutFounderProviderProfiles,
  readFounderProviderProfiles,
  resolveFounderProviderProfile,
  writeFounderProviderProfiles,
  type FounderProviderProfile,
} from '../upstream/overlay/src/vs/workbench/contrib/void/common/founderProviderProfiles.ts';

const profile = (index: number): FounderProviderProfile => ({
  id: `profile-${index}`,
  label: `Personal ${index}`,
  baseUrl: `https://provider-${index}.test/v1/`,
  apiKey: `sk-${index}`,
  model: `model-${index}`,
  headers: { 'X-Profile': String(index) },
});

describe('Founder personal provider profiles', () => {
  it('round-trips profiles while preserving legacy custom headers', () => {
    const encoded = writeFounderProviderProfiles(
      JSON.stringify({ 'X-Legacy': 'kept' }),
      [profile(1)],
    );
    assert.deepEqual(readFounderProviderProfiles(encoded), [
      { ...profile(1), baseUrl: 'https://provider-1.test/v1' },
    ]);
    assert.deepEqual(JSON.parse(headersWithoutFounderProviderProfiles(encoded)), {
      'X-Legacy': 'kept',
    });
  });

  it('resolves the dropdown label to its actual endpoint and model', () => {
    const encoded = writeFounderProviderProfiles('{}', [profile(2)]);
    const resolved = resolveFounderProviderProfile(encoded, 'Personal 2');
    assert.equal(resolved?.baseUrl, 'https://provider-2.test/v1');
    assert.equal(resolved?.model, 'model-2');
    assert.equal(resolveFounderProviderProfile(encoded, 'founder-os-auto'), null);
  });

  it('rejects unsafe records, duplicate labels, and profiles beyond the V1 limit', () => {
    const encoded = JSON.stringify({
      [FOUNDER_PROVIDER_PROFILES_KEY]: [
        ...Array.from({ length: 8 }, (_, index) => profile(index)),
        { ...profile(9), label: 'Personal 0' },
        { ...profile(10), baseUrl: 'file:///secret' },
        { ...profile(11), label: 'founder-os-override' },
      ],
    });
    const profiles = readFounderProviderProfiles(encoded);
    assert.equal(profiles.length, FOUNDER_PROVIDER_PROFILE_LIMIT);
    assert.equal(new Set(profiles.map((entry) => entry.label.toLowerCase())).size, profiles.length);
  });

  it('fails closed when the encrypted settings field is malformed', () => {
    assert.deepEqual(readFounderProviderProfiles('{not-json'), []);
    assert.equal(headersWithoutFounderProviderProfiles('{not-json'), '{}');
  });
});

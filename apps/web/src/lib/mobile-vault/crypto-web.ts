/** Browser/WebView AES-GCM — matches @dcf/founder-vault node crypto layout (iv | tag | ciphertext). */

export async function deriveVaultKey(nodeToken: string, nodeId: string): Promise<CryptoKey> {
  const material = `${nodeToken}:${nodeId}:founder-vault-v1`;
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(material));
  return crypto.subtle.importKey('raw', digest, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}

export async function encryptVaultJson(plainJson: string, key: CryptoKey): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encWithTag = new Uint8Array(
    await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(plainJson)),
  );
  const tag = encWithTag.slice(encWithTag.length - 16);
  const data = encWithTag.slice(0, encWithTag.length - 16);
  const out = new Uint8Array(12 + 16 + data.length);
  out.set(iv, 0);
  out.set(tag, 12);
  out.set(data, 28);
  return btoa(String.fromCharCode(...out));
}

export async function decryptVaultJson(blobBase64: string, key: CryptoKey): Promise<string> {
  const buf = Uint8Array.from(atob(blobBase64), (c) => c.charCodeAt(0));
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const data = buf.subarray(28);
  const combined = new Uint8Array(data.length + 16);
  combined.set(data, 0);
  combined.set(tag, data.length);
  const dec = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, combined);
  return new TextDecoder().decode(dec);
}

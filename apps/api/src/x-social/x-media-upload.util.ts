import { buildOAuth1Header, OAuth1Credentials } from './x-oauth1';

const UPLOAD_URL = 'https://upload.twitter.com/1.1/media/upload.json';

export async function uploadTweetImage(
  creds: OAuth1Credentials,
  imageBuffer: Buffer,
  filename = 'conviction.png',
): Promise<{ ok: true; mediaId: string } | { ok: false; reason: string }> {
  const form = new FormData();
  const blob = new Blob([new Uint8Array(imageBuffer)], { type: 'image/png' });
  form.append('media', blob, filename);

  const authorization = buildOAuth1Header('POST', UPLOAD_URL, creds);

  const res = await fetch(UPLOAD_URL, {
    method: 'POST',
    headers: { Authorization: authorization },
    body: form,
  });

  if (!res.ok) {
    const body = await res.text();
    return { ok: false, reason: `Media upload failed (${res.status}): ${body.slice(0, 200)}` };
  }

  const data = (await res.json()) as { media_id_string?: string };
  const mediaId = data.media_id_string;
  if (!mediaId) return { ok: false, reason: 'No media_id_string returned' };

  return { ok: true, mediaId };
}

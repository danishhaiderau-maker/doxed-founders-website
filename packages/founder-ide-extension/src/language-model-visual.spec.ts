import assert from 'node:assert/strict';
import test from 'node:test';
import {
  appendManagedVisualContext,
  languageModelImageCount,
  languageModelVisualAttachments,
} from './language-model-visual';

test('extracts supported image parts without retaining binary data', () => {
  const attachments = languageModelVisualAttachments([
    {
      content: [
        { value: 'inspect this' },
        { mimeType: 'image/png', data: new Uint8Array([1, 2, 3]) },
      ],
    },
    {
      content: [
        { mimeType: 'application/json', data: new Uint8Array([4]) },
        { mimeType: 'image/jpeg', data: new Uint8Array([5, 6]) },
      ],
    },
  ]);

  assert.deepEqual(
    attachments.map(({ messageIndex, name, mimeType, dataBase64 }) => ({
      messageIndex,
      name,
      mimeType,
      dataBase64,
    })),
    [
      {
        messageIndex: 0,
        name: 'message-1-screenshot-1.png',
        mimeType: 'image/png',
        dataBase64: 'AQID',
      },
      {
        messageIndex: 1,
        name: 'message-2-screenshot-1.jpg',
        mimeType: 'image/jpeg',
        dataBase64: 'BQY=',
      },
    ],
  );
});

test('injects descriptions only into the message that owns each image', () => {
  const attachments = languageModelVisualAttachments([
    {
      content: [
        { mimeType: 'image/webp', data: new Uint8Array([1]) },
      ],
    },
    { content: [{ value: 'second message' }] },
  ]);
  const messages = appendManagedVisualContext(
    ['change the circled area', 'second message'],
    attachments,
    {
      descriptions: [
        {
          name: attachments[0].name,
          description: 'A red circle surrounds the account button.',
        },
      ],
      provider: 'glm',
      model: 'glm-4.6v-flash',
      route: 'founder-managed-vision',
    },
  );

  assert.match(messages[0], /red circle surrounds the account button/);
  assert.match(messages[0], /untrusted user-provided content/);
  assert.equal(messages[1], 'second message');
});

test('rejects unsupported images, missing descriptions, and more than four images', () => {
  assert.throws(
    () => languageModelVisualAttachments([
      {
        content: [
          { mimeType: 'image/gif', data: new Uint8Array([1]) },
        ],
      },
    ]),
    /PNG, JPEG, and WebP/,
  );

  const attachments = languageModelVisualAttachments([
    {
      content: [
        { mimeType: 'image/png', data: new Uint8Array([1]) },
      ],
    },
  ]);
  assert.throws(
    () => appendManagedVisualContext(
      ['look'],
      attachments,
      {
        descriptions: [],
        provider: 'glm',
        model: 'glm-4.6v-flash',
        route: 'founder-managed-vision',
      },
    ),
    /could not understand/,
  );

  assert.throws(
    () => languageModelVisualAttachments([
      {
        content: Array.from({ length: 5 }, () => ({
          mimeType: 'image/png',
          data: new Uint8Array([1]),
        })),
      },
    ]),
    /up to four screenshots/,
  );
});

test('counts only supported image data parts', () => {
  assert.equal(
    languageModelImageCount({
      content: [
        { mimeType: 'image/png', data: new Uint8Array([1]) },
        { mimeType: 'application/json', data: new Uint8Array([2]) },
        { value: 'text' },
      ],
    }),
    1,
  );
});

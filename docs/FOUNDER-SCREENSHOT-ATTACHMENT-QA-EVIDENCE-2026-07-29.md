# Founder Screenshot Attachment QA Evidence - 2026-07-29

## Accepted scope

The installed Founder IDE uses one screenshot composer for Ask, Plan, Build,
Debug, and Team. The same bounded path accepts PNG, JPEG, and WebP images
through:

- the file picker;
- clipboard paste; and
- drag and drop.

Every accepted image is previewed before sending and can be removed without
changing typed text. A send performs one permitted vision pass, converts the
result to explicitly untrusted text context, and never forwards raw image bytes
to the text-only working model.

## Native installed-app evidence

The installed `Founder IDE` was restarted with a local DevTools endpoint and
tested through the real Electron workbench.

Evidence:

- `C:\Users\user\Desktop\Final Bots\artifacts\installed-attachment-qa-20260729-complete\installed-attachment-final.json`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-attachment-qa-20260729-complete\installed-attachment-final.png`

Measured result:

- browse preview/remove: PASS;
- clipboard paste preview/remove: PASS;
- drag/drop preview/remove: PASS;
- Ask, Plan, Build, Debug, and Team mode labels present in the shared composer:
  PASS;
- console errors: 0;
- page exceptions: 0.

The reusable runner is
`packages/founder-ide/scripts/installed-attachment-qa.mjs`. It now fails when
any of the three input paths, preview removal, shared mode contract, console
check, or page-exception check fails.

## Annotation contract evidence

The product path explicitly asks permitted vision models for layout, visible
text, circles, arrows, boxes, underlines, labels, highlights, likely intent,
confidence, and uncertainty. Structured results are bounded and marked as
untrusted visual evidence before entering chat.

Checks completed:

- Founder extension: 214/214;
- Founder extension TypeScript: PASS;
- managed visual API and controller: 7/7;
- Founder overlay: 22/22.

The tests prove that circle and arrow evidence, marked targets, visible text,
confidence, and likely intent survive the provider boundary and reach the
working model without exposing provider keys.

## Honest remaining gate

This machine currently has no Ollama service on port `11434`, and no permitted
managed vision key was added during QA. Therefore a real external model has not
yet interpreted a fresh screenshot containing a circle, arrow, and label.
Deterministic contract tests are not misreported as live semantic vision.

Close that final gate only after a founder-selected Personal AI vision profile,
a local multimodal Ollama model, or an explicitly authorized managed vision
route is available. The live proof must identify the marked target and OCR text
from a real annotated screenshot while preserving the same privacy and receipt
rules.

# Changelog

## 0.2.0 — 2026-11-10

- Add the model-scoped Auto Display Filter: prepared email responses become visual cards automatically after completion, without a manual toolbar click.
- Include named Edit email, Save draft and Send email buttons inside the card, delegating to the existing native confirmation and authenticated Action workflow.
- Replace generic toolbar sparkles with pencil, save, send and reload icons.
- Permit unknown recipients while preparing a card; require a valid To address before saving/sending.
- Disable unavailable mailbox operations and add clear Microsoft 365 connection status.
- Extend mocked workflow and sandbox browser tests; refresh both theme screenshots.

## 0.1.2 — 2026-11-10

- Replace the lavender editor with a neutral Minimal layout in light and dark.
- Add recipient chips, on-demand CC/BCC fields, compact accessible formatting icons and a quiet priority menu. Both list types remain supported.
- Synchronize the app theme through a fixed native bridge when opening the card; use system theme before the bridge is registered.
- Insert links inline without requiring modal or form permissions in the sandbox.
- Preserve uncertain/sent status notices while editing and allow the card height to shrink when content shrinks.
- Add real light/dark editor screenshots and browser checks for theme switching, recipient editing, lists, links and narrow layouts.

## 0.1.1 — 2026-11-03

- Use English by default for editor fields, formatting controls, action labels, confirmations and status messages.
- Update installation examples and browser-test content to English. Email content continues to follow the user’s requested language.

## 0.1.0 — 2026-11-03

- Original inline editor with Dutch labels and native message Actions.
- Per-user existing MCP connection for draft creation, updates and optional confirmed sending.
- Persistent owner-bound state, version checks and fail-closed uncertain outcomes.
- Offline mocked workflow tests; live Open WebUI/Microsoft 365 validation outstanding.

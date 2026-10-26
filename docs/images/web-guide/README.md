# English Web guide screenshots

These are browser captures of real local LiveOption and QFinZero pages.
They accompany [the English setup guide](../../getting-started.md).

- Interface: English, light theme, 1440 px desktop viewport.
- Backend: public QFinZero v1.0.2, commit `2d8dda5`, with its synthetic sample.
- LiveOption run: QZSP, 2025-03-24, USD 100,000, 60-minute decisions.
- Model: a local deterministic HTTP fixture returning `[]` (HOLD). No
  performance claim or paid model comparison is represented.
- Credentials: password controls are empty; account tokens, provider secrets
  and QFinZero keys are not included.

The one-day run has six decisions and no fills. Its report intentionally shows
**Insufficient data** for statistics that require more observations.

## Regenerate

Use an isolated local instance with a completed synthetic run. Start both Web
interfaces in English; QFinZero's hub must serve its built console (or adopt a
separately running local console). Install frontend development dependencies
and the Playwright Chromium browser, then run from `frontend/`:

```bash
pnpm exec playwright install chromium
node scripts/capture-guide.mjs \
  --url http://127.0.0.1:3001 \
  --run-id YOUR_COMPLETED_SYNTHETIC_RUN_ID \
  --token-env-file /path/to/LiveOption/.liveoption/runtime.env \
  --qfz-url http://127.0.0.1:19777 \
  --qfz-login-file /private/path/to/qfz-docs-login.json
```

The optional QFinZero login file is a private JSON object with `username` and
`password` for your local documentation account. Keep it outside the repository.
Omit both `--qfz-*` arguments to capture only LiveOption. The script uses normal
authentication and real APIs. It does not submit tasks or call model inference,
and refuses runs outside the synthetic QZSP/ACME/BOLT universe.

`--only filename.png,other.png` refreshes selected images. `capture.json`
records the captured paths, language and image dimensions without credentials.
Review every image before publishing, especially run logs and data directories.

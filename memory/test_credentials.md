# Test Credentials — Zanelvo Dev Studio

App location: `/app/apps/zanelvo-dev-studio/` (monorepo `sassinfo05-sudo/freebuff`).
Runs via the managed supervisor: backend on :8001 (uvicorn `server:app`), frontend on :3000 (Vite).
`/app/backend` and `/app/frontend` are symlinks into the app so the managed supervisor picks it up.

## Admin login (single-user)
- Preview URL: https://d4f82446-87b6-4b25-a5dc-2927f10fd055.preview.emergentagent.com
- Password: `605561`  (env `ADMIN_PASSWORD` in `apps/zanelvo-dev-studio/backend/.env`)
- No username — one password gates the whole app.

## Configured secrets (in DB, encrypted, and/or backend .env)
- `EMERGENT_UNIVERSAL_KEY` set in `.env` → all agent roles set to `auto_provider=true`, so the
  whole pipeline runs on the Emergent Universal Key (Claude/GPT/Gemini via proxy). Verified live.
- `github_pat` stored via Settings→Secrets (classic PAT, user `sassinfo05-sudo`). Not in git.
- Native ANTHROPIC/OPENAI/GEMINI keys: NOT set (optional; Emergent covers them).

## Test repo / project
- GitHub repo: `sassinfo05-sudo/tester` (private, created via the app's new-repo flow).
- Dev Studio project id: `6aa687ffcaf70cc396348938`.
- E2E task pushed branch `ai/add-getting-started-to-readme-4479b2` (commit `3cbe2e0`).

## Notes
- `.env` is gitignored; never commit secrets. Variable names documented in `.env.example`.

---
paths:
  - "frontend/**/*.{ts,tsx,css}"
  - "frontend/vite.config.ts"
  - "frontend/package.json"
---

# Frontend

Vite + React + TypeScript. TanStack Query for fetching, TanStack Table for
the tag grid, Tailwind v4, shadcn/ui.

## Tailwind is v4, not v3

v4 removed the CLI and the PostCSS-based setup. Applying v3 instructions
fails silently: no error, classes simply don't apply.

- Setup is the `@tailwindcss/vite` plugin plus `@import "tailwindcss";` in
  `src/index.css`.
- There is no `tailwind.config.js` and no `postcss.config.js`.
- Never run `npx tailwindcss init` — the command no longer exists.
- Theme customization goes in CSS via `@theme`, not a JS config.

## API types are generated

`src/api/schema.d.ts` is generated from `../api/esl-pricing-api.yaml` by
`npm run types`. Never hand-edit it, and never hand-write interfaces that
duplicate it. If a type is missing, the spec is wrong — fix the spec.

Call the API through the typed client in `src/api/client.ts`
(`openapi-fetch`), not bare `fetch`.

## Conventions

- Function components with hooks. No class components.
- Server state lives in TanStack Query, not `useState` or context.
- The tag table is the primary surface. Default sort is "needs attention"
  (failed command, low battery, not heard from, still sending) — the
  recurring operational task is "which tags need a person."
- Money arrives as integer cents. Format at the render boundary only.
- Dev requests go through the Vite proxy (`/v1` → localhost:8000).
  Never hardcode an absolute backend URL; production is same-origin.
---
paths:
  - "frontend/**/*.{ts,tsx,css}"
  - "frontend/vite.config.ts"
  - "frontend/package.json"
---

# Frontend

Vite + React + TypeScript. TanStack Query for fetching, TanStack Table for
the "All Tags" grid, Tailwind v4, shadcn/ui.

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
- Two views. **Labels** is the landing view: tags needing a person (never
  heard, not responding, failed update, low battery, weak signal; see
  `lib/format.ts::tagStatus`) come first, worst first, then the rest;
  battery ascending within each group, because the recurring task is
  "which tags need batteries". **Products** is the item-first workflow:
  browse catalog items, open one to see and manage its labels.
- Colors come only from the theme tokens in `src/index.css` (`text-ink`,
  `bg-surface`, `text-ok`/`warn`/`bad`, ...), never raw palette classes like
  `text-gray-500`: the light/dark switch only re-colors tokens. Reuse the
  primitives in `src/ui.tsx`.
- Money arrives as integer cents. Format at the render boundary only.
- Dev requests go through the Vite proxy (`/v1` → localhost:8001).
  Never hardcode an absolute backend URL; production is same-origin.
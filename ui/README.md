# agi UI

Next.js 15 + Tailwind 4 + shadcn/ui scaffold for the `agi` orchestrator board.

## Requirements

- Node 20+ (see `.nvmrc`). Tailwind 4's native binding will fail on Node 18.

## Develop

    npm install
    npm run dev          # http://localhost:3000

The dev server proxies `/api/*` to the FastAPI backend at
`http://localhost:8000` (override with `AGI_API_URL`).

## Scripts

- `npm run dev` — start the dev server on port 3000
- `npm run build` — production build
- `npm run start` — serve the production build
- `npm run lint` — ESLint
- `npm run typecheck` — `tsc --noEmit`

## Layout

- `app/` — App Router entry points
- `components/` — feature components (`sidebar`, `board`)
- `components/ui/` — shadcn/ui primitives
- `lib/utils.ts` — `cn()` helper

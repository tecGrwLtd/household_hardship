# Frontend

React + TypeScript (Vite), TanStack Query for data, plain CSS with theme
variables. No chart library: the charts are small HTML bar charts that follow
the theme. Fonts (IBM Plex Sans, Source Serif 4) are bundled, so the app makes
no calls to outside hosts.

## Run it

```bash
npm install
npm run dev        # http://localhost:3000, proxies /api to the API on :8080
npm test           # vitest
npm run build      # type-check + production build into dist/
```

In Docker (`docker compose up -d --build` at the project root) nginx serves
the build on :3000 and proxies `/api` to the API container.

## How it is organised

| Path | What |
|---|---|
| `src/state/auth.tsx` | Sign-in, the signed-in user and role |
| `src/state/filters.tsx` | The sidebar's global filters. Each page calls `usePageFilters([...])` to say which filters it uses; the sidebar greys out the rest |
| `src/state/theme.tsx` | Light / dark, remembered per browser, first choice from the system setting |
| `src/api/` | Fetch wrapper (`/api` + bearer token), React Query hooks, response types |
| `src/components/` | Layout (sidebar), UI kit, charts, the case view shared by the application page and the review queue |
| `src/pages/` | One file per screen |
| `src/lib/format.ts` | Labels and number/date formatting — one place for wording |
| `src/styles.css` | Design tokens for both themes and all component styles |

## Roles

| | Admin (programme manager) | Caseworker |
|---|---|---|
| Home | Dashboard | My work |
| Applications, review queue, households, new application | ✓ (all) | ✓ (own by default) |
| Funding cycles, models & monitoring | ✓ | — |

The API enforces the same rules; the menu only mirrors them.

# CAssist Codebase Review

Verified current-application findings only. Deployment work, unsupported claims, and findings already
handled by the current code have been removed.

## High priority

### 1. Image panning has no boundary clamp

[`frontend/src/components/source-preview.jsx`](frontend/src/components/source-preview.jsx)

A zoomed image can be dragged completely outside the viewport. Clamp its position so part of the
image always remains reachable without requiring Reset.

### 2. Standard API requests have no client timeout

[`frontend/src/lib/api.js`](frontend/src/lib/api.js)

Queries receive cancellation signals from TanStack Query, but ordinary mutation requests can remain
pending indefinitely. Add an `AbortController` timeout for standard API requests without applying an
unreasonably short timeout to file uploads.

## Medium priority

### 3. Version-conflict feedback exposes implementation language

[`frontend/src/pages/review-page.jsx`](frontend/src/pages/review-page.jsx)

A `409` refreshes the result, but the existing mutation error can still display the server's conflict
message. Replace it with concise feedback such as: “A newer version was loaded. Review your change
and try again.”

### 4. Dashboard loading uses text instead of a structural loading state

[`frontend/src/pages/dashboard-page.jsx`](frontend/src/pages/dashboard-page.jsx)

The dashboard has an empty state, but its initial load shows only “Loading documents…”. A small
table-row skeleton would reduce layout shift and better communicate loading.

### 5. PDF and image previews use different zoom limits

[`frontend/src/components/source-preview.jsx`](frontend/src/components/source-preview.jsx)

Image zoom allows 0.5–4.0× while PDF zoom allows 0.5–3.0×. Share the zoom constants unless the
difference is intentional and documented.

### 6. Source-panel visibility does not persist

[`frontend/src/pages/review-page.jsx`](frontend/src/pages/review-page.jsx)

The Show/Hide original preference resets after navigation. Persist it locally as a user-interface
preference.

## Minor enhancements

### 7. Drag-and-drop lacks an active visual state

[`frontend/src/pages/upload-page.jsx`](frontend/src/pages/upload-page.jsx)

Track drag enter/leave and highlight the drop zone while files are over it.

### 8. Correction textareas do not auto-grow

[`frontend/src/pages/review-page.jsx`](frontend/src/pages/review-page.jsx)

Long extracted values require scrolling or manual resizing. Auto-grow the editor up to a sensible
maximum height.

### 9. PDF viewer lacks zoom keyboard shortcuts

[`frontend/src/components/source-preview.jsx`](frontend/src/components/source-preview.jsx)

Add keyboard-accessible `+` and `-` zoom actions while the viewer is focused. Extracted values already
use native buttons and therefore support Enter/Space for copying.

### 10. Theme toggle does not expose pressed state

[`frontend/src/App.jsx`](frontend/src/App.jsx)

Add `aria-pressed` so assistive technology can determine whether the explicit dark theme is active.

### 11. Provider comparison lacks diff highlighting

[`frontend/src/pages/compare-page.jsx`](frontend/src/pages/compare-page.jsx)

The page shows both provider results but does not visually identify differing values. Add a compact
field/table comparison rather than requiring manual scanning.

### 12. Session-cleanup batch size is not configurable

[`backend/app/services/session_cleanup.py`](backend/app/services/session_cleanup.py)

The cleanup function defaults to 100 records. Move the worker's chosen batch size into settings if
operational measurements show the default needs tuning.

## Verified controls

| Requirement | Status |
|---|---|
| Request ID on API responses | Implemented |
| `Cache-Control: no-store` on sensitive dynamic responses | Implemented |
| Workspace authorization on document and result operations | Implemented |
| CSRF validation on unsafe mutations | Implemented |
| Mutation idempotency-key validation | Implemented |
| EXIF orientation handling | Implemented |
| React Strict Mode | Implemented |
| System-aware theme default | Implemented |
| Development-only comparison route | Implemented |
| PostgreSQL-backed integration tests with rollback isolation | Implemented |

## Recommended order

1. Clamp image panning.
2. Add standard-request timeouts.
3. Improve version-conflict feedback.
4. Add a dashboard loading skeleton.
5. Align preview zoom limits.

# CAssist Codebase Review

Verified current-application findings only. Deployment work, unsupported claims, and findings already
handled by the current code have been removed.

## Medium priority

### 1. Source-panel visibility does not persist

[`frontend/src/pages/review-page.jsx`](frontend/src/pages/review-page.jsx)

The Show/Hide original preference resets after navigation. Persist it locally as a user-interface
preference.

## Minor enhancements

### 2. Drag-and-drop lacks an active visual state

[`frontend/src/pages/upload-page.jsx`](frontend/src/pages/upload-page.jsx)

Track drag enter/leave and highlight the drop zone while files are over it.

### 3. Correction textareas do not auto-grow

[`frontend/src/pages/review-page.jsx`](frontend/src/pages/review-page.jsx)

Long extracted values require scrolling or manual resizing. Auto-grow the editor up to a sensible
maximum height.

### 4. PDF viewer lacks zoom keyboard shortcuts

[`frontend/src/components/source-preview.jsx`](frontend/src/components/source-preview.jsx)

Add keyboard-accessible `+` and `-` zoom actions while the viewer is focused. Extracted values already
use native buttons and therefore support Enter/Space for copying.

### 5. Theme toggle does not expose pressed state

[`frontend/src/App.jsx`](frontend/src/App.jsx)

Add `aria-pressed` so assistive technology can determine whether the explicit dark theme is active.

### 6. Provider comparison lacks diff highlighting

[`frontend/src/pages/compare-page.jsx`](frontend/src/pages/compare-page.jsx)

The page shows both provider results but does not visually identify differing values. Add a compact
field/table comparison rather than requiring manual scanning.

### 7. Session-cleanup batch size is not configurable

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

1. Persist source-panel visibility.
2. Add drag-and-drop feedback.
3. Auto-grow correction textareas.
4. Add PDF zoom shortcuts.
5. Expose the theme toggle's pressed state.

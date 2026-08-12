# CAssist Frontend — Remaining UI/UX Findings

Verified against the current frontend on 2026-08-12. The first ten findings were implemented in two enhancement batches. This file now tracks the five remaining fully supported findings.

## P3 — Low-risk polish

### 11. Network loss is not distinguished from other request failures

The API client reports browser fetch failures without identifying offline state. Detect `navigator.onLine` for messaging and listen for online/offline changes so users know whether retrying can succeed.

**Where:** [`frontend/src/lib/api.js`](frontend/src/lib/api.js)

### 12. Session checking is an unbranded text-only screen

While authentication is being checked, the entire app shows only `Checking your session…`. Add a compact spinner and CAssist identity while keeping the message available to assistive technology.

**Where:** [`frontend/src/App.jsx`](frontend/src/App.jsx)

### 13. The shared button base uses an unrestricted transition

The base button applies `transition-all`, allowing unrelated property changes to animate. Restrict it to the properties the component intentionally animates, while leaving the separate sun/moon transform animation intact.

**Where:** [`frontend/src/components/ui/button.jsx`](frontend/src/components/ui/button.jsx)

### 14. The application has no configured favicon

No favicon is linked from the HTML document, so browser tabs use a generic icon.

**Where:** [`frontend/index.html`](frontend/index.html)

### 15. Mobile browser chrome has no theme colour

The HTML document has no `theme-color` metadata for light or dark mode. Add colour-scheme-aware metadata so supported mobile browser chrome matches the application.

**Where:** [`frontend/index.html`](frontend/index.html)

## Removed from the original audit

Removed findings included already-fixed claims (dashboard skeletons, upload drag feedback, toast position, compare diffs), partially true claims (all loading states, all preview keyboard support), duplicate findings, and optional preferences presented as defects (approval confirmation, kinetic panning, custom scrollbars, tooltip arrows, a global route-loading bar, settings expansion, and broad shortcut/onboarding proposals).

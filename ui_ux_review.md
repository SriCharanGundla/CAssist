# CAssist Frontend — Remaining UI/UX Findings

Verified against the current frontend on 2026-08-12. The first five findings were implemented in the first enhancement batch. This file now tracks the ten remaining fully supported findings.

## P2 — Accessibility and operational polish

### 6. The authenticated shell has no skip-to-content link

Keyboard users must traverse the header controls on every route before reaching the page content. Add a focus-visible skip link targeting the main content region.

**Where:** [`frontend/src/App.jsx`](frontend/src/App.jsx)

### 7. Frequently used controls have small touch targets

Dashboard actions, preview toolbar buttons, the upload remove action, the account trigger, and the dialog close action are roughly 24–28 CSS pixels. Keep the compact visual icons if desired, but enlarge their interactive hit areas for touch use.

**Where:** [`frontend/src/components/ui/button.jsx`](frontend/src/components/ui/button.jsx), [`frontend/src/App.jsx`](frontend/src/App.jsx), [`frontend/src/components/ui/dialog.jsx`](frontend/src/components/ui/dialog.jsx)

### 8. Browser titles do not identify the current route or document

Every route leaves the document title as `CAssist`, so tabs and browser history do not distinguish dashboard, upload, settings, comparison, or a specific review. Set a route-aware title and include the source filename on review when available.

**Where:** [`frontend/index.html`](frontend/index.html), [`frontend/src/App.jsx`](frontend/src/App.jsx)

### 9. Source-preview icon controls have no visible tooltips

The image and PDF toolbars correctly provide accessible names, but sighted users must infer zoom, reset, fit, rotate, and page-navigation actions from icons alone. Add the existing tooltip component around these controls.

**Where:** [`frontend/src/components/source-preview.jsx`](frontend/src/components/source-preview.jsx)

### 10. Active processing uses fixed two-second polling

Dashboard rows and the development comparison page poll every two seconds until completion. Use a modest adaptive interval that stays responsive initially and backs off for longer runs.

**Where:** [`frontend/src/pages/dashboard-page.jsx`](frontend/src/pages/dashboard-page.jsx), [`frontend/src/pages/compare-page.jsx`](frontend/src/pages/compare-page.jsx)

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

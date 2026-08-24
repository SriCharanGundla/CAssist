const TERMINAL_RUN_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "needs_confirmation",
  "unsupported",
])

export function documentDeletionDisabled(document, run = document.latest_run) {
  if (run) return !TERMINAL_RUN_STATUSES.has(run.status)
  return ["upload_pending", "uploaded", "processing"].includes(document.status)
}

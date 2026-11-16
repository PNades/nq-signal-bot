// Leaving a form with unsaved changes asks first (pages/accounts.md, settings.md). A page with a dirty form registers
// how many changes it holds; the app's navigate() asks `pendingChanges()` before switching page and shows its own
// dialog (never the browser's confirm()); a reload or tab close gets the browser's own "leave site?" prompt.
let source: (() => number) | null = null;

export function registerLeaveGuard(fn: () => number): () => void {
  source = fn;
  const onBefore = (e: BeforeUnloadEvent) => { if ((source?.() ?? 0) > 0) { e.preventDefault(); } };
  window.addEventListener("beforeunload", onBefore);
  return () => { if (source === fn) source = null; window.removeEventListener("beforeunload", onBefore); };
}

export const pendingChanges = () => source?.() ?? 0;

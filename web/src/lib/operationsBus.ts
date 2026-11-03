"use client";

/** A tiny cross-component signal so the Task Panel can refresh the instant
 * something happens elsewhere in the app, instead of waiting on its own
 * fixed polling interval. Any component that creates/approves/dismisses an
 * operation calls notifyOperationsChanged() right after; TaskPanel listens
 * and refreshes immediately, with the interval kept only as a fallback
 * (e.g. an operation progressing on its own between user actions). */

const EVENT_NAME = "pyxie:operations-changed";

export function notifyOperationsChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(EVENT_NAME));
}

export function onOperationsChanged(callback: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(EVENT_NAME, callback);
  return () => window.removeEventListener(EVENT_NAME, callback);
}

/** Same idea for the sidebar's count badges (Health, Rightsizing, Maintenance...): call this right after an action
 * that changes what they count (acknowledge / dismiss / restore) so they update at once instead of on the 30 s poll. */
const BADGES_EVENT = "pyxie:badges-changed";

export function notifyBadgesChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(BADGES_EVENT));
}

export function onBadgesChanged(callback: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(BADGES_EVENT, callback);
  return () => window.removeEventListener(BADGES_EVENT, callback);
}

/** Walking away from a preview (cancel, back, close) rejects its plan, so nothing is left waiting for approval and the locks it holds are freed.
 * Only a plan still awaiting approval is touched; anything already started is left alone. */
export function abandonPlan(id: string) {
  return fetch(`/api/operations/${id}/cancel`, { method: "POST" }).catch(() => {}).finally(() => notifyOperationsChanged());
}

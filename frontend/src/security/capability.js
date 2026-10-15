import { ref } from "vue";
// Capabilities remain in memory, tied to their destination page.
let capability = null;
export const capabilityRevision = ref(0);
const routes = ["/login", "/board/bind"];
const valid = (value) => /^[A-Za-z0-9_-]{43}$/.test(value);
export function takeFragment(location, history) {
  if (!routes.includes(location.pathname)) return null;
  const value = location.hash.slice(1);
  history.replaceState(null, "", location.pathname + location.search);
  return valid(value) ? value : null;
}
export function captureCapability() {
  if (!routes.includes(window.location.pathname)) return;
  capability = {
    path: window.location.pathname,
    value: takeFragment(window.location, window.history),
  };
  capabilityRevision.value++;
}
export function captureRouteCapability(path, hash) {
  if (!routes.includes(path) || !hash) return false;
  const value = hash.slice(1);
  capability = { path, value: valid(value) ? value : null };
  capabilityRevision.value++;
  return true;
}
export function consumeCapability(path) {
  if (!capability || capability.path !== path) return null;
  const value = capability.value;
  capability = null;
  return value;
}

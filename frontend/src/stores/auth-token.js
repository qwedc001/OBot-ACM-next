let value = "";
export function getCsrf() {
  return value;
}
export function setCsrf(token) {
  value = typeof token === "string" ? token : "";
}

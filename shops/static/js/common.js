// Small helpers shared by every page. Plain JS, no libraries.

// x402 headers are base64 of JSON. These convert both ways (UTF-8 safe).
function b64ToJson(b64) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}
function jsonToB64(obj) {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin);
}

// 0x1234567890abcdef... -> 0x1234…abcd
function shortHex(value) {
  if (!value) return "—";
  return value.length > 12 ? value.slice(0, 6) + "…" + value.slice(-4) : value;
}

function usd(n, digits = 2) {
  return "$" + Number(n || 0).toFixed(digits);
}

function randomHex(bytes) {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return "0x" + Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
}

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

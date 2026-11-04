// UI-only keys must also work on phones using an ordinary LAN HTTP address.
let sequence = 0;
export function localId(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${++sequence}`;
}

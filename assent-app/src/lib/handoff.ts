// Module-scoped staging for the PDF bytes handed off from Home → Sign. Using a
// Map avoids the 60 MB hit of round-tripping a 10 MB Uint8Array through
// JSON.stringify/sessionStorage.

interface Pending {
  filename: string;
  bytes: Uint8Array;
}

const pending = new Map<string, Pending>();

export function stagePending(file: Pending): string {
  const token = crypto.randomUUID();
  pending.set(token, file);
  return token;
}

export function takePending(token: string): Pending | null {
  const entry = pending.get(token);
  if (entry) pending.delete(token);
  return entry ?? null;
}

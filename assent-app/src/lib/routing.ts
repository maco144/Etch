// Host-aware routing helpers. The same bundle is served from two places:
//
//   etch.locker/assent/*   (trust-architecture framing)
//   assent.to/*            (consumer framing — grammar: "I assent to …")
//
// The bundle's asset URLs live under /assent/ regardless (vite base="/assent/"),
// but the application-level routes differ by host. We detect which prefix is
// active from the current location at startup and use it when linking.

export function basePrefix(): string {
  return window.location.pathname.startsWith("/assent") ? "/assent" : "";
}

export function pathFor(route: "home" | "sign"): string {
  const b = basePrefix();
  if (route === "home") return b || "/";
  return `${b}/${route}`;
}

export function verifyPath(id: string): string {
  // /verify/* is not host-specific — Caddy serves it via the same SPA on both
  // etch.locker and assent.to.
  return `/verify/${id}`;
}

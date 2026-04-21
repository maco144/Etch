import { Link, Outlet, useLocation } from "react-router-dom";
import { pathFor } from "./lib/routing";

export default function App() {
  const { pathname } = useLocation();
  const isVerify = pathname.startsWith("/verify");
  const homePath = pathFor("home");

  return (
    <div className="min-h-screen flex flex-col">
      <nav className="sticky top-0 z-10 backdrop-blur bg-bg/80 border-b border-border">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <Link to={homePath} className="flex items-center gap-2 no-underline">
            <span className="font-mono text-base font-bold tracking-wider text-text">
              ETCH
            </span>
            <span className="text-text-muted">/</span>
            <span className="text-sm text-text-dim">Assent</span>
          </Link>
          <div className="flex items-center gap-6 text-sm">
            {!isVerify && (
              <Link to={homePath} className="text-text-dim hover:text-text no-underline">
                Sign
              </Link>
            )}
            <a
              href="https://etch.locker"
              className="text-text-dim hover:text-text no-underline"
            >
              About Etch
            </a>
            <a
              href="https://github.com/maco144/Etch"
              className="text-text-dim hover:text-text no-underline"
            >
              GitHub
            </a>
          </div>
        </div>
      </nav>

      <main className="flex-1">
        <Outlet />
      </main>

      <footer className="border-t border-border mt-16">
        <div className="max-w-6xl mx-auto px-6 py-8 flex flex-col sm:flex-row justify-between gap-4 text-xs text-text-muted">
          <div>
            Etch Assent — permanent proof of agreement. No vendor to trust.
          </div>
          <div className="flex gap-4">
            <span>Client-side only</span>
            <span>·</span>
            <span>Hash-only storage</span>
            <span>·</span>
            <a
              href="https://etch.locker"
              className="hover:text-text-dim no-underline"
            >
              etch.locker
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}

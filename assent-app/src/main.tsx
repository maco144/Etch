import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import App from "./App";
import Home from "./routes/Home";
import Send from "./routes/Send";
import Verify from "./routes/Verify";
import "./styles/index.css";

// Sign pulls in PDF.js and pdf-lib (~800 KB minified). Lazy-load it so Home
// and Verify can render without that cost — the heavy libs only arrive once
// the user actually navigates into the signing flow.
const Sign = lazy(() => import("./routes/Sign"));

const SignRoute = () => (
  <Suspense fallback={<RouteFallback label="Loading signer…" />}>
    <Sign />
  </Suspense>
);

function RouteFallback({ label }: { label: string }) {
  return (
    <div className="min-h-[60vh] flex items-center justify-center text-sm text-text-dim">
      {label}
    </div>
  );
}

const root = document.getElementById("root")!;

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          {/* etch.locker paths */}
          <Route path="/assent" element={<Home />} />
          <Route path="/assent/send" element={<Send />} />
          <Route path="/assent/sign" element={<SignRoute />} />
          <Route path="/assent/sign/:documentId" element={<SignRoute />} />
          {/* assent.to paths */}
          <Route path="/" element={<Home />} />
          <Route path="/send" element={<Send />} />
          <Route path="/sign" element={<SignRoute />} />
          <Route path="/sign/:documentId" element={<SignRoute />} />
          {/* Shared */}
          <Route path="/verify" element={<Verify />} />
          <Route path="/verify/:recordOrDocId" element={<Verify />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);

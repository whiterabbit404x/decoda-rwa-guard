"use client";

import { useEffect } from "react";

type DashboardErrorBoundaryProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function DashboardErrorBoundary({
  error,
  reset,
}: DashboardErrorBoundaryProps) {
  useEffect(() => {
    console.error("Dashboard route error", error);
  }, [error]);

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-700 shadow-sm">
      <p className="font-medium text-slate-900">Dashboard unavailable.</p>
      <p className="mt-1">Please retry.</p>
      <button
        type="button"
        onClick={reset}
        className="mt-3 inline-flex items-center rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
      >
        Retry
      </button>
    </div>
  );
}

"use client";

type DashboardErrorBoundaryProps = {
  reset: () => void;
};

export default function DashboardErrorBoundary({
  reset,
}: DashboardErrorBoundaryProps) {
  const hardReloadDashboard = () => {
    window.location.assign('/dashboard');
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-700 shadow-sm">
      <p className="font-medium text-slate-900">
        Dashboard unavailable. Please retry.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          onClick={reset}
          className="inline-flex items-center rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
        >
          Retry
        </button>
        <button
          type="button"
          onClick={hardReloadDashboard}
          className="inline-flex items-center rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
        >
          Hard reload
        </button>
      </div>
    </div>
  );
}

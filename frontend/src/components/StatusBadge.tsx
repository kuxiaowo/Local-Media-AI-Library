const tone: Record<string, string> = {
  done: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  running: 'bg-blue-50 text-blue-700 border-blue-200',
  queued: 'bg-slate-50 text-slate-700 border-slate-200',
  failed: 'bg-red-50 text-red-700 border-red-200',
  missing: 'bg-amber-50 text-amber-700 border-amber-200',
  needs_reanalysis: 'bg-violet-50 text-violet-700 border-violet-200',
  metadata_done: 'bg-cyan-50 text-cyan-700 border-cyan-200',
  embedding_pending: 'bg-teal-50 text-teal-700 border-teal-200',
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${
        tone[status] ?? 'border-slate-200 bg-slate-50 text-slate-700'
      }`}
    >
      {status}
    </span>
  );
}

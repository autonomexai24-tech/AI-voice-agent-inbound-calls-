import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-normal text-ink">{title}</h1>
        {description ? <p className="mt-1 max-w-2xl text-sm text-slate-600">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-lg border border-line bg-panel shadow-panel ${className}`}>{children}</section>;
}

export function StatCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail?: string;
}) {
  return (
    <Panel className="p-5">
      <div className="text-sm font-medium text-slate-600">{label}</div>
      <div className="mt-2 text-3xl font-semibold text-ink">{value}</div>
      {detail ? <div className="mt-2 text-sm text-slate-500">{detail}</div> : null}
    </Panel>
  );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="rounded-lg border border-line bg-white p-8 text-sm text-slate-600 shadow-panel">
      <div className="h-2 w-28 animate-pulse rounded bg-slate-200" />
      <div className="mt-4 h-3 w-full max-w-lg animate-pulse rounded bg-slate-100" />
      <div className="mt-2 h-3 w-2/3 animate-pulse rounded bg-slate-100" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-danger">
      <div className="font-semibold">Request failed</div>
      <div className="mt-1 text-red-700">{message}</div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="focus-ring mt-3 rounded-md border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-danger"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-white p-8 text-center">
      <div className="font-semibold text-ink">{title}</div>
      {detail ? <div className="mt-1 text-sm text-slate-500">{detail}</div> : null}
    </div>
  );
}

export function StatusPill({ value }: { value?: string }) {
  const normalized = (value || "unknown").toLowerCase();
  const classes =
    normalized === "confirmed" || normalized === "booked" || normalized === "positive"
      ? "border-emerald-200 bg-emerald-50 text-ok"
      : normalized === "failed" || normalized === "negative" || normalized === "cancelled"
        ? "border-red-200 bg-red-50 text-danger"
        : "border-slate-200 bg-slate-50 text-slate-700";

  return (
    <span className={`inline-flex min-h-7 items-center rounded-full border px-2.5 text-xs font-semibold ${classes}`}>
      {normalized}
    </span>
  );
}

export function Button({
  children,
  type = "button",
  disabled,
  onClick,
  variant = "primary",
}: {
  children: ReactNode;
  type?: "button" | "submit";
  disabled?: boolean;
  onClick?: () => void;
  variant?: "primary" | "secondary";
}) {
  const classes =
    variant === "primary"
      ? "bg-brand text-white hover:bg-teal-800"
      : "border border-line bg-white text-ink hover:bg-slate-50";

  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`focus-ring inline-flex min-h-10 items-center justify-center rounded-md px-4 text-sm font-semibold transition ${classes} disabled:cursor-not-allowed disabled:opacity-60`}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  label: string;
  value: string | number;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        type={type}
        placeholder={placeholder}
        className="focus-ring mt-2 min-h-11 w-full rounded-md border border-line bg-white px-3 py-2 text-sm text-ink shadow-sm"
      />
    </label>
  );
}

export function TextArea({
  label,
  value,
  onChange,
  rows = 6,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        placeholder={placeholder}
        className="focus-ring mt-2 w-full resize-y rounded-md border border-line bg-white px-3 py-2 text-sm leading-6 text-ink shadow-sm"
      />
    </label>
  );
}

export function SaveBar({ saving, saved, error }: { saving: boolean; saved: boolean; error: string }) {
  return (
    <div className="flex flex-col gap-3 border-t border-line px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-h-5 text-sm">
        {error ? <span className="font-medium text-danger">{error}</span> : null}
        {saved && !error ? <span className="font-medium text-ok">Saved</span> : null}
      </div>
      <Button type="submit" disabled={saving}>
        {saving ? "Saving" : "Save changes"}
      </Button>
    </div>
  );
}

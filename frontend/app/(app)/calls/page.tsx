"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { formatDateTime, formatDuration, maskPhone } from "@/lib/format";
import type { CallLog } from "@/lib/types";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Panel, StatusPill } from "@/components/ui";

export default function CallsPage() {
  const [calls, setCalls] = useState<CallLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  function load() {
    setLoading(true);
    setError("");
    apiFetch<CallLog[]>("/api/logs")
      .then((rows) => {
        setCalls(rows);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err.message);
        setLoading(false);
      });
  }

  useEffect(load, []);

  if (loading) return <LoadingBlock label="Loading calls" />;
  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <>
      <PageHeader title="Calls" description="Tenant-scoped call logs from the FastAPI backend." />
      {calls.length ? (
        <Panel className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-sm">
              <thead className="bg-slate-50 text-left text-xs font-semibold uppercase text-slate-500">
                <tr>
                  <th className="px-5 py-3">Caller</th>
                  <th className="px-5 py-3">Duration</th>
                  <th className="px-5 py-3">Sentiment</th>
                  <th className="px-5 py-3">Summary</th>
                  <th className="px-5 py-3">Time</th>
                  <th className="px-5 py-3">Transcript</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line bg-white">
                {calls.map((call, index) => (
                  <tr key={call.id || index}>
                    <td className="whitespace-nowrap px-5 py-4 font-medium text-ink">
                      {maskPhone(call.phone_number || call.caller_phone)}
                    </td>
                    <td className="whitespace-nowrap px-5 py-4 text-slate-600">{formatDuration(call.duration_seconds)}</td>
                    <td className="whitespace-nowrap px-5 py-4">
                      <StatusPill value={call.sentiment} />
                    </td>
                    <td className="max-w-md px-5 py-4 text-slate-600">
                      <div className="line-clamp-2">{call.summary || "-"}</div>
                    </td>
                    <td className="whitespace-nowrap px-5 py-4 text-slate-600">{formatDateTime(call.created_at)}</td>
                    <td className="whitespace-nowrap px-5 py-4">
                      {call.id ? (
                        <a
                          href={`/api/logs/${call.id}/transcript`}
                          className="focus-ring rounded-md border border-line px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                        >
                          Download
                        </a>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : (
        <EmptyState title="No call logs" detail="Calls will appear here once the runtime writes them." />
      )}
    </>
  );
}

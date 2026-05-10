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
  const [recordingUrls, setRecordingUrls] = useState<Record<string, string>>({});
  const [recordingLoading, setRecordingLoading] = useState<Record<string, boolean>>({});
  const [recordingError, setRecordingError] = useState<Record<string, string>>({});

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

  async function loadRecording(recordingId: string) {
    setRecordingLoading((current) => ({ ...current, [recordingId]: true }));
    setRecordingError((current) => ({ ...current, [recordingId]: "" }));
    try {
      const response = await apiFetch<{ url: string; expires_in: number }>(`/api/recordings/${recordingId}/playback`);
      setRecordingUrls((current) => ({ ...current, [recordingId]: response.url }));
    } catch (err) {
      setRecordingError((current) => ({
        ...current,
        [recordingId]: err instanceof Error ? err.message : "Recording unavailable",
      }));
    } finally {
      setRecordingLoading((current) => ({ ...current, [recordingId]: false }));
    }
  }

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
                  <th className="px-5 py-3">Recording</th>
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
                    <td className="min-w-56 px-5 py-4">
                      {call.recording_id && call.recording_upload_status === "uploaded" ? (
                        <div className="space-y-2">
                          {recordingUrls[call.recording_id] ? (
                            <audio controls src={recordingUrls[call.recording_id]} className="h-10 w-56 max-w-full" />
                          ) : (
                            <button
                              type="button"
                              onClick={() => loadRecording(call.recording_id as string)}
                              disabled={recordingLoading[call.recording_id]}
                              className="focus-ring rounded-md border border-line px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                            >
                              {recordingLoading[call.recording_id] ? "Loading" : "Play"}
                            </button>
                          )}
                          {recordingError[call.recording_id] ? (
                            <div className="text-xs font-medium text-danger">{recordingError[call.recording_id]}</div>
                          ) : null}
                        </div>
                      ) : call.recording_upload_status ? (
                        <StatusPill value={call.recording_upload_status} />
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

"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { formatDateTime, formatDuration, maskPhone } from "@/lib/format";
import type { Booking, CallLog, Stats } from "@/lib/types";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Panel, StatCard, StatusPill } from "@/components/ui";

type DashboardData = {
  stats: Stats;
  calls: CallLog[];
  bookings: Booking[];
};

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  function load() {
    setLoading(true);
    setError("");
    Promise.all([
      apiFetch<Stats>("/api/stats"),
      apiFetch<CallLog[]>("/api/logs"),
      apiFetch<Booking[]>("/api/bookings"),
    ])
      .then(([stats, calls, bookings]) => {
        setData({ stats, calls, bookings });
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err.message);
        setLoading(false);
      });
  }

  useEffect(load, []);

  if (loading) {
    return <LoadingBlock label="Loading dashboard" />;
  }

  if (error || !data) {
    return <ErrorState message={error || "Dashboard unavailable"} onRetry={load} />;
  }

  const recentCalls = data.calls.slice(0, 6);
  const upcomingBookings = data.bookings.slice(0, 5);

  return (
    <>
      <PageHeader title="Dashboard" description="Live operational view for calls, bookings, and receptionist activity." />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Total calls" value={data.stats.total_calls || 0} detail="Tenant scoped" />
        <StatCard label="Bookings" value={data.stats.total_bookings || 0} detail="From booking records" />
        <StatCard label="Avg duration" value={formatDuration(data.stats.avg_duration || 0)} detail="Across logged calls" />
        <StatCard label="Booking rate" value={`${data.stats.booking_rate || 0}%`} detail="Bookings per call" />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Panel>
          <div className="border-b border-line px-5 py-4">
            <h2 className="font-semibold text-ink">Recent calls</h2>
          </div>
          {recentCalls.length ? (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-line text-sm">
                <thead className="bg-slate-50 text-left text-xs font-semibold uppercase text-slate-500">
                  <tr>
                    <th className="px-5 py-3">Caller</th>
                    <th className="px-5 py-3">Duration</th>
                    <th className="px-5 py-3">Sentiment</th>
                    <th className="px-5 py-3">Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {recentCalls.map((call, index) => (
                    <tr key={call.id || index}>
                      <td className="px-5 py-4 font-medium text-ink">{maskPhone(call.phone_number || call.caller_phone)}</td>
                      <td className="px-5 py-4 text-slate-600">{formatDuration(call.duration_seconds)}</td>
                      <td className="px-5 py-4">
                        <StatusPill value={call.sentiment} />
                      </td>
                      <td className="px-5 py-4 text-slate-600">{formatDateTime(call.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-5">
              <EmptyState title="No calls logged yet" detail="Inbound calls will appear after the backend writes call logs." />
            </div>
          )}
        </Panel>

        <Panel>
          <div className="border-b border-line px-5 py-4">
            <h2 className="font-semibold text-ink">Bookings</h2>
          </div>
          {upcomingBookings.length ? (
            <div className="divide-y divide-line">
              {upcomingBookings.map((booking, index) => (
                <div key={booking.id || index} className="px-5 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate font-medium text-ink">{booking.patient_name || booking.name || "Unknown"}</div>
                      <div className="mt-1 text-sm text-slate-600">
                        {maskPhone(booking.patient_phone || booking.phone_number)}
                      </div>
                    </div>
                    <StatusPill value={booking.status} />
                  </div>
                  <div className="mt-2 text-sm text-slate-500">
                    {formatDateTime(booking.start_time || booking.created_at)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-5">
              <EmptyState title="No bookings yet" />
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

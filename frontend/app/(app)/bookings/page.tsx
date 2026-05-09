"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { formatDateTime, maskPhone } from "@/lib/format";
import type { Booking } from "@/lib/types";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Panel, StatusPill } from "@/components/ui";

export default function BookingsPage() {
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  function load() {
    setLoading(true);
    setError("");
    apiFetch<Booking[]>("/api/bookings")
      .then((rows) => {
        setBookings(rows);
        setLoading(false);
      })
      .catch((err: Error) => {
        setError(err.message);
        setLoading(false);
      });
  }

  useEffect(load, []);

  if (loading) return <LoadingBlock label="Loading bookings" />;
  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <>
      <PageHeader title="Bookings" description="Appointments created by the receptionist booking flow." />
      {bookings.length ? (
        <Panel className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-sm">
              <thead className="bg-slate-50 text-left text-xs font-semibold uppercase text-slate-500">
                <tr>
                  <th className="px-5 py-3">Patient</th>
                  <th className="px-5 py-3">Phone</th>
                  <th className="px-5 py-3">Start time</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Cal.com UID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line bg-white">
                {bookings.map((booking, index) => (
                  <tr key={booking.id || index}>
                    <td className="whitespace-nowrap px-5 py-4 font-medium text-ink">
                      {booking.patient_name || booking.name || "Unknown"}
                    </td>
                    <td className="whitespace-nowrap px-5 py-4 text-slate-600">
                      {maskPhone(booking.patient_phone || booking.phone_number)}
                    </td>
                    <td className="whitespace-nowrap px-5 py-4 text-slate-600">
                      {formatDateTime(booking.start_time || booking.created_at)}
                    </td>
                    <td className="whitespace-nowrap px-5 py-4">
                      <StatusPill value={booking.status} />
                    </td>
                    <td className="max-w-xs truncate px-5 py-4 text-slate-600">{booking.cal_booking_uid || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : (
        <EmptyState title="No bookings" detail="Successful bookings will appear here after Cal.com writes complete." />
      )}
    </>
  );
}

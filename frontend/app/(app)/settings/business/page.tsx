"use client";

import { FormEvent } from "react";
import { Field, LoadingBlock, PageHeader, Panel, SaveBar, TextArea } from "@/components/ui";
import { useConfigForm } from "@/lib/useConfigForm";

export default function BusinessSettingsPage() {
  const { config, loading, saving, error, saved, setField, save } = useConfigForm();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await save({
      business_name: config.business_name || "",
      business_phone: config.business_phone || "",
      business_hours_json: config.business_hours_json || "",
      transfer_number: config.transfer_number || "",
      cal_event_type_id: config.cal_event_type_id || "",
    });
  }

  if (loading) return <LoadingBlock label="Loading business settings" />;

  return (
    <>
      <PageHeader title="Business settings" description="Tenant business details used by transfer and booking workflows." />
      <Panel>
        <form onSubmit={submit}>
          <div className="grid gap-5 p-5">
            <div className="grid gap-5 sm:grid-cols-2">
              <Field
                label="Business name"
                value={config.business_name || ""}
                onChange={(value) => setField("business_name", value)}
              />
              <Field
                label="Inbound DID"
                value={config.business_phone || ""}
                onChange={(value) => setField("business_phone", value)}
              />
            </div>
            <div className="grid gap-5 sm:grid-cols-2">
              <Field
                label="Transfer number"
                value={config.transfer_number || ""}
                onChange={(value) => setField("transfer_number", value)}
              />
              <Field
                label="Cal.com event type ID"
                value={config.cal_event_type_id || ""}
                onChange={(value) => setField("cal_event_type_id", value)}
              />
            </div>
            <TextArea
              label="Business hours JSON"
              value={config.business_hours_json || ""}
              onChange={(value) => setField("business_hours_json", value)}
              rows={8}
            />
          </div>
          <SaveBar saving={saving} saved={saved} error={error} />
        </form>
      </Panel>
    </>
  );
}

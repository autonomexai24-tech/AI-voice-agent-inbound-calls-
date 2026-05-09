"use client";

import { FormEvent } from "react";
import { Field, LoadingBlock, PageHeader, Panel, SaveBar, TextArea } from "@/components/ui";
import { useConfigForm } from "@/lib/useConfigForm";

export default function AgentSettingsPage() {
  const { config, loading, saving, error, saved, setField, save } = useConfigForm();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await save({
      first_line: config.first_line || "",
      agent_instructions: config.agent_instructions || "",
      llm_model: config.llm_model || "gpt-4o-mini",
      stt_min_endpointing_delay: config.stt_min_endpointing_delay || 0.5,
    });
  }

  if (loading) return <LoadingBlock label="Loading agent settings" />;

  return (
    <>
      <PageHeader title="Agent settings" description="Conversation behavior used by the realtime voice agent." />
      <Panel>
        <form onSubmit={submit}>
          <div className="grid gap-5 p-5">
            <TextArea
              label="First line"
              rows={3}
              value={config.first_line || ""}
              onChange={(value) => setField("first_line", value)}
            />
            <TextArea
              label="Agent instructions"
              rows={9}
              value={config.agent_instructions || ""}
              onChange={(value) => setField("agent_instructions", value)}
            />
            <div className="grid gap-5 sm:grid-cols-2">
              <Field
                label="LLM model"
                value={config.llm_model || "gpt-4o-mini"}
                onChange={(value) => setField("llm_model", value)}
              />
              <Field
                label="Endpointing delay"
                type="number"
                value={config.stt_min_endpointing_delay || 0.5}
                onChange={(value) => setField("stt_min_endpointing_delay", value)}
              />
            </div>
          </div>
          <SaveBar saving={saving} saved={saved} error={error} />
        </form>
      </Panel>
    </>
  );
}

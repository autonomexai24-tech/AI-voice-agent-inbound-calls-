"use client";

import { FormEvent } from "react";
import { Field, LoadingBlock, PageHeader, Panel, SaveBar } from "@/components/ui";
import { useConfigForm } from "@/lib/useConfigForm";

const languagePresets = [
  { id: "multilingual", label: "Multilingual" },
  { id: "english", label: "English" },
  { id: "hinglish", label: "Hinglish" },
  { id: "kannada", label: "Kannada" },
  { id: "marathi", label: "Marathi" },
  { id: "hindi", label: "Hindi" },
];

export default function VoiceSettingsPage() {
  const { config, loading, saving, error, saved, setField, save } = useConfigForm();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await save({
      tts_voice: config.tts_voice || "kavya",
      tts_language: config.tts_language || "hi-IN",
      lang_preset: config.lang_preset || "multilingual",
    });
  }

  if (loading) return <LoadingBlock label="Loading voice settings" />;

  return (
    <>
      <PageHeader title="Voice settings" description="Speech and multilingual defaults used by the active tenant." />
      <Panel>
        <form onSubmit={submit}>
          <div className="grid gap-5 p-5">
            <div className="grid gap-5 sm:grid-cols-2">
              <Field
                label="TTS voice"
                value={config.tts_voice || "kavya"}
                onChange={(value) => setField("tts_voice", value)}
              />
              <Field
                label="TTS language"
                value={config.tts_language || "hi-IN"}
                onChange={(value) => setField("tts_language", value)}
              />
            </div>
            <fieldset>
              <legend className="mb-3 text-sm font-medium text-slate-700">Language preset</legend>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {languagePresets.map((preset) => {
                  const active = (config.lang_preset || "multilingual") === preset.id;
                  return (
                    <button
                      key={preset.id}
                      type="button"
                      onClick={() => setField("lang_preset", preset.id)}
                      className={`focus-ring min-h-12 rounded-md border px-4 text-left text-sm font-semibold transition ${
                        active ? "border-brand bg-teal-50 text-brand" : "border-line bg-white text-slate-700 hover:bg-slate-50"
                      }`}
                    >
                      {preset.label}
                    </button>
                  );
                })}
              </div>
            </fieldset>
          </div>
          <SaveBar saving={saving} saved={saved} error={error} />
        </form>
      </Panel>
    </>
  );
}

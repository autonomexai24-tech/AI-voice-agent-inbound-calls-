"use client";

import { useEffect, useMemo, useState } from "react";
import { apiFetch, postJson } from "@/lib/api";
import type { Config } from "@/lib/types";

type State = {
  config: Config;
  loading: boolean;
  saving: boolean;
  error: string;
  saved: boolean;
};

export function useConfigForm() {
  const [state, setState] = useState<State>({
    config: {},
    loading: true,
    saving: false,
    error: "",
    saved: false,
  });

  useEffect(() => {
    let active = true;

    apiFetch<Config>("/api/config")
      .then((config) => {
        if (active) {
          setState({ config, loading: false, saving: false, error: "", saved: false });
        }
      })
      .catch((error: Error) => {
        if (active) {
          setState((current) => ({ ...current, loading: false, error: error.message }));
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const actions = useMemo(
    () => ({
      setField<K extends keyof Config>(key: K, value: Config[K]) {
        setState((current) => ({
          ...current,
          saved: false,
          config: { ...current.config, [key]: value },
        }));
      },
      async save(payload: Partial<Config>) {
        setState((current) => ({ ...current, saving: true, error: "", saved: false }));
        try {
          await postJson<{ status: string }>("/api/config", payload);
          setState((current) => ({
            ...current,
            saving: false,
            saved: true,
            config: { ...current.config, ...payload },
          }));
        } catch (error) {
          setState((current) => ({
            ...current,
            saving: false,
            error: error instanceof Error ? error.message : "Save failed",
          }));
        }
      },
    }),
    [],
  );

  return { ...state, ...actions };
}

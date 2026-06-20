"use client";

import { createContext, useContext, useState, useMemo, useCallback, type ReactNode } from "react";
import { DEFAULT_MP3_BITRATE } from "@/lib/constants";

export interface ExportConfig {
  outputFormat: string;
  mp3Bitrate: number;
  dryRun: boolean;
  overwrite: boolean;
  verifyAudio: boolean;
  requireOutputMatch: boolean;
}

interface ExportConfigActions {
  setOutputFormat: (v: string) => void;
  setMp3Bitrate: (v: number) => void;
  setDryRun: (v: boolean) => void;
  setOverwrite: (v: boolean) => void;
  setVerifyAudio: (v: boolean) => void;
  setRequireOutputMatch: (v: boolean) => void;
}

type ExportConfigContextValue = ExportConfig & ExportConfigActions;

const ExportConfigContext = createContext<ExportConfigContextValue | null>(null);

export function useExportConfig(): ExportConfigContextValue {
  const ctx = useContext(ExportConfigContext);
  if (!ctx) throw new Error("useExportConfig must be used within ExportConfigProvider");
  return ctx;
}

export function ExportConfigProvider({ children }: { children: ReactNode }) {
  const [outputFormat, setOutputFormat] = useState("playable");
  const [mp3Bitrate, setMp3Bitrate] = useState(DEFAULT_MP3_BITRATE);
  const [dryRun, setDryRun] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [verifyAudio, setVerifyAudio] = useState(false);
  const [requireOutputMatch, setRequireOutputMatch] = useState(false);

  const value = useMemo(
    () => ({
      outputFormat,
      mp3Bitrate,
      dryRun,
      overwrite,
      verifyAudio,
      requireOutputMatch,
      setOutputFormat,
      setMp3Bitrate,
      setDryRun,
      setOverwrite,
      setVerifyAudio,
      setRequireOutputMatch,
    }),
    [outputFormat, mp3Bitrate, dryRun, overwrite, verifyAudio, requireOutputMatch]
  );

  return (
    <ExportConfigContext.Provider value={value}>
      {children}
    </ExportConfigContext.Provider>
  );
}

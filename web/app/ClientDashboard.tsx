"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { StatusCard } from "./sections/StatusCard";
import { ActionCenter } from "./sections/ActionCenter";
import { TargetSearch } from "./sections/TargetSearch";
import { SourceList } from "./sections/SourceList";
import { ExportConfigBar } from "./sections/ExportConfigBar";
import { SettingsPanel } from "./sections/SettingsPanel";
import { JobProgress } from "./sections/JobProgress";
import { Button } from "./components/ui/Button";
import { Badge } from "./components/ui/Badge";
import { ExportConfigProvider, useExportConfig } from "./contexts/ExportConfigContext";
import { usePreflight } from "@/hooks/usePreflight";
import { useSources } from "@/hooks/useSources";
import { useTargetSearch } from "@/hooks/useTargetSearch";
import { useJob } from "@/hooks/useJob";
import {
  startExport,
  startTargetJob,
  openPath,
  type ExportPayload,
  type TargetPayload,
} from "@/lib/api";
import { DEFAULT_MP3_BITRATE } from "@/lib/constants";
import { ArrowsClockwise } from "@phosphor-icons/react";

function DashboardContent() {
  const config = useExportConfig();
  const {
    data: preflight,
    loading: preflightLoading,
    error: preflightError,
    refetch,
  } = usePreflight(6000);
  const sources = useSources();
  const targetSearch = useTargetSearch();
  const job = useJob(1200);

  const [showTarget, setShowTarget] = useState(false);
  const [targetQuery, setTargetQuery] = useState("");
  const [targetArtist, setTargetArtist] = useState("");
  const [targetVersion, setTargetVersion] = useState("lossless/flac");
  const [targetTimeout, setTargetTimeout] = useState(0);

  const runtime = useMemo(
    () => ({
      cacheDir: preflight?.cacheDir || "",
      outputDir: preflight?.outputDir || "",
      deviceNode: preflight?.deviceNode || "",
      mp3Bitrate: preflight?.mp3Bitrate || DEFAULT_MP3_BITRATE,
      mp3TranscoderFound: preflight?.mp3TranscoderFound ?? false,
    }),
    [preflight]
  );

  useEffect(() => {
    if (runtime.cacheDir && !sources.rows.length && !sources.loading) {
      sources.refetch(runtime.cacheDir);
    }
  }, [runtime.cacheDir]);

  const handleRefresh = useCallback(async () => {
    await refetch(true);
    if (runtime.cacheDir) {
      await sources.refetch(runtime.cacheDir);
    }
  }, [refetch, runtime.cacheDir, sources]);

  const buildExportPayload = useCallback((): ExportPayload => {
    let selectedSources = sources.rows
      .filter((row) => row.selected && row.cacheUuid)
      .map((row) => ({
        cacheUuid: row.cacheUuid!,
        format: row.format || config.outputFormat,
      }));

    if (selectedSources.length === 0) {
      selectedSources = sources.rows
        .filter((row) => row.cacheUuid)
        .map((row) => ({
          cacheUuid: row.cacheUuid!,
          format: row.format || config.outputFormat,
        }));
    }

    return {
      cacheDir: runtime.cacheDir,
      outputDir: runtime.outputDir,
      deviceNode: runtime.deviceNode,
      format: config.outputFormat,
      mp3Bitrate: config.mp3Bitrate,
      dryRun: config.dryRun,
      overwrite: config.overwrite,
      verifyAudio: config.verifyAudio,
      requireOutputMatch: config.requireOutputMatch,
      selectedSources,
    };
  }, [sources.rows, config, runtime]);

  const handleStartExport = useCallback(async () => {
    const payload = buildExportPayload();
    if (payload.selectedSources?.length === 0) return;
    const snapshot = await startExport(payload);
    job.start(snapshot);
  }, [buildExportPayload, job]);

  const handleStartTarget = useCallback(async () => {
    const selected = targetSearch.matches.find(
      (m) => m.trackId === targetSearch.selectedTrackId
    );
    if (!selected) return;
    const payload: TargetPayload = {
      cacheDir: runtime.cacheDir,
      outputDir: runtime.outputDir,
      deviceNode: runtime.deviceNode,
      query: targetQuery || selected.title,
      artist: targetArtist || selected.artists,
      trackId: selected.trackId,
      target: targetVersion,
      timeout: targetTimeout,
      stableSeconds: 1,
      interval: 3,
      format: config.outputFormat === "playable" ? "auto" : config.outputFormat,
      selectionFormat: config.outputFormat === "playable" ? "auto" : config.outputFormat,
      mp3Bitrate: config.mp3Bitrate,
      dryRun: config.dryRun,
      overwrite: config.overwrite,
      verifyAudio: config.verifyAudio,
      requireOutputMatch: config.requireOutputMatch,
    };
    const snapshot = await startTargetJob(payload);
    job.start(snapshot);
  }, [targetSearch, runtime, targetQuery, targetArtist, config, targetVersion, targetTimeout, job]);

  const ready = preflight?.ready ?? false;
  const jobRunning = job.job?.status === "running";

  return (
    <div className="min-h-screen bg-surface pb-12">
      <DashboardHeader
        ready={ready}
        preflightLoading={preflightLoading}
        onRefresh={handleRefresh}
        disabled={preflightLoading || sources.loading || jobRunning}
      />

      <main className="mx-auto max-w-6xl space-y-4 px-4 pt-6">
        {preflightError && (
          <div className="rounded-[var(--radius-md)] border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {preflightError}
          </div>
        )}

        <StatusCard
          data={preflight}
          loading={preflightLoading}
          onOpenOutput={() => runtime.outputDir && openPath(runtime.outputDir, true)}
        />

        <ExportConfigBar
          mp3TranscoderFound={runtime.mp3TranscoderFound}
          outputDir={runtime.outputDir}
          exportable={sources.exportable}
          selected={sources.selected}
          ready={ready}
          jobRunning={jobRunning}
          onExport={handleStartExport}
        />

        <JobProgress state={job} />

        <ActionCenter showTarget={showTarget} onShowTarget={() => setShowTarget((v) => !v)} />

        {showTarget ? (
          <TargetSearch
            state={targetSearch}
            indexedQualities={preflight?.sources?.indexedQualities || []}
            cacheDir={runtime.cacheDir}
            query={targetQuery}
            artist={targetArtist}
            target={targetVersion}
            timeout={targetTimeout}
            jobRunning={jobRunning}
            onQueryChange={setTargetQuery}
            onArtistChange={setTargetArtist}
            onTargetChange={setTargetVersion}
            onTimeoutChange={setTargetTimeout}
            onSearch={() =>
              targetSearch.search({
                cacheDir: runtime.cacheDir,
                query: targetQuery,
                artist: targetArtist,
                target: targetVersion,
              })
            }
            onStart={handleStartTarget}
          />
        ) : (
          <SourceList
            state={sources}
            mp3TranscoderFound={runtime.mp3TranscoderFound}
            jobRunning={jobRunning}
            defaultFormat={config.outputFormat}
          />
        )}

        <SettingsPanel />
      </main>
    </div>
  );
}

function DashboardHeader({
  ready,
  preflightLoading,
  onRefresh,
  disabled,
}: {
  ready: boolean;
  preflightLoading: boolean;
  onRefresh: () => void;
  disabled: boolean;
}) {
  return (
    <header className="border-b border-zinc-200 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
        <h1 className="text-lg font-semibold tracking-tight text-zinc-900">
          SodaMusic Cache Export
        </h1>
        <div className="flex items-center gap-2">
          <Badge tone={ready ? "success" : preflightLoading ? "neutral" : "warning"}>
            {ready ? "就绪" : preflightLoading ? "检测中" : "未就绪"}
          </Badge>
          <Button
            variant="ghost"
            size="sm"
            aria-label="重新检测环境"
            onClick={onRefresh}
            disabled={disabled}
          >
            <ArrowsClockwise size={18} />
          </Button>
        </div>
      </div>
    </header>
  );
}

export function ClientDashboard() {
  return (
    <ExportConfigProvider>
      <DashboardContent />
    </ExportConfigProvider>
  );
}

export type CheckItem = {
  label: string;
  ok: boolean;
  detail: string;
  path: string;
};

export type SourceRow = {
  trackId: string;
  title: string;
  artists: string;
  album: string;
  durationMs?: number;
  quality: string;
  codecType: string;
  extension: string;
  bitrate?: number;
  sourceSize?: number;
  cacheUuid?: string;
  encrypted?: boolean;
  hasSpade?: boolean;
  uncachedBest?: {
    quality: string;
    codecType?: string;
    extension?: string;
  } | null;
  cachedCandidates?: SourceCandidate[];
  selected?: boolean;
  format?: string;
  defaultFormat?: string;
};

export type SourceCandidate = {
  cacheUuid: string;
  quality: string;
  bitrate?: number;
  extension: string;
  codecType: string;
  sourceSize?: number;
  encrypted?: boolean;
};

export type SourcesResponse = {
  rows: SourceRow[];
  total: number;
  exportable: number;
  uncachedHigher: number;
  indexedQualities: string[];
  cachedQualities: string[];
  error: string;
};

export type PreflightPayload = {
  apiVersion: number;
  ready: boolean;
  cacheDir: string;
  outputDir: string;
  deviceNode: string;
  checks: CheckItem[];
  warnings: string[];
  errors: string[];
  sources: SourcesResponse;
  mp3Bitrate: number;
  mp3TranscoderFound: boolean;
  nodeFound: boolean;
  platform: string;
  script: string;
};

export type PreflightStatusPayload = Omit<PreflightPayload, "sources"> & {
  sources: {
    total: number;
    exportable: number;
    uncachedHigher: number;
    indexedQualities: string[];
    cachedQualities: string[];
    error: string;
  };
};

export type TargetSummary = {
  trackId: string;
  title: string;
  artists: string;
  album: string;
  durationMs?: number;
  indexedLabels: string[];
  cachedLabels: string[];
  targetIndexed: boolean;
  targetCached: boolean;
  targetRank: number;
  targetCacheUuids: string[];
  targetCachedFiles: {
    cacheUuid: string;
    resourceId: string;
    quality: string;
    codecType: string;
    extension: string;
    sourceSize?: number;
    indexedSize?: number;
    encrypted: boolean;
    hasSpade: boolean;
  }[];
};

export type TargetSearchResponse = {
  matches: TargetSummary[];
  total: number;
  limit: number;
  target: string;
  error: string;
};

export type BatchPreflightRow = {
  status: string;
  status_code: number;
  target: {
    query?: string;
    track_id?: string;
    title?: string;
    artist?: string;
    target?: string;
  };
  target_cache_uuids?: string[];
};

export type BatchPreflightResponse = {
  rows: BatchPreflightRow[];
  total: number;
  limit: number;
  truncated: boolean;
  ok: boolean;
  counts: Record<string, number>;
  error: string;
};

export type JobMetrics = {
  total: number;
  current: number;
  exported: number;
  skipped: number;
  phase: string;
  message: string;
};

export type JobSnapshot = {
  id: string;
  status: "running" | "completed" | "failed";
  returncode: number | null;
  cacheDir: string;
  outputDir: string;
  startedAt: number;
  finishedAt: number | null;
  logs: string[];
  metrics: JobMetrics;
  error: string;
};

export type ExportPayload = {
  cacheDir: string;
  outputDir: string;
  deviceNode: string;
  keyMode?: "device" | "raw";
  rawKey?: string;
  format: string;
  mp3Bitrate: number;
  dryRun?: boolean;
  overwrite?: boolean;
  verifyAudio?: boolean;
  requireOutputMatch?: boolean;
  limit?: number;
  selectedSources?: { cacheUuid: string; format: string }[];
};

export type TargetPayload = {
  cacheDir: string;
  outputDir: string;
  deviceNode: string;
  keyMode?: "device" | "raw";
  rawKey?: string;
  trackId?: string;
  query?: string;
  artist?: string;
  target: string;
  timeout?: number;
  stableSeconds?: number;
  interval?: number;
  format?: string;
  selectionFormat?: string;
  mp3Bitrate?: number;
  dryRun?: boolean;
  overwrite?: boolean;
  verifyAudio?: boolean;
  once?: boolean;
  allowSizeMismatch?: boolean;
  requireOutputMatch?: boolean;
};

const API_BASE = "";

async function requestJson<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      ...options,
    });
  } catch (err) {
    throw new Error(
      `网络请求失败: ${err instanceof Error ? err.message : String(err)}`
    );
  }

  let payload: Record<string, unknown>;
  try {
    payload = await response.json();
  } catch {
    if (!response.ok) {
      throw new Error(`请求失败 (HTTP ${response.status})`);
    }
    throw new Error("服务器返回了无效的 JSON 响应");
  }

  if (!response.ok) {
    const message =
      (payload.errors as string[] | undefined)?.join("\n") ||
      (payload.error as string | undefined) ||
      `请求失败 (HTTP ${response.status})`;
    throw new Error(message);
  }
  return payload as T;
}

export function getPreflightStatus(force = false) {
  return requestJson<PreflightStatusPayload>(
    `/api/preflight-status${force ? "?force=1" : ""}`
  );
}

export function getPreflight(force = false) {
  return requestJson<PreflightPayload>(
    `/api/preflight${force ? "?force=1" : ""}`
  );
}

export function scanSources(cacheDir: string) {
  return requestJson<SourcesResponse>("/api/sources", {
    method: "POST",
    body: JSON.stringify({ cacheDir }),
  });
}

export function searchTargets(payload: {
  cacheDir: string;
  query?: string;
  artist?: string;
  target: string;
  limit?: number;
}) {
  return requestJson<TargetSearchResponse>("/api/target-search", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function preflightBatchTargets(payload: {
  cacheDir: string;
  targets: { trackId?: string; title?: string; artist?: string; query?: string; target: string }[];
  limit?: number;
}) {
  return requestJson<BatchPreflightResponse>("/api/batch-target-preflight", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function startExport(payload: ExportPayload) {
  return requestJson<JobSnapshot>("/api/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function startTargetJob(payload: TargetPayload) {
  return requestJson<JobSnapshot>("/api/target-jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getJob(jobId: string) {
  return requestJson<JobSnapshot>(`/api/jobs/${jobId}`);
}

export function validateExport(payload: ExportPayload) {
  return requestJson<{ errors: string[] }>("/api/validate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function openPath(path: string, create = false) {
  return requestJson<{ opened: boolean }>("/api/open", {
    method: "POST",
    body: JSON.stringify({ path, create }),
  });
}

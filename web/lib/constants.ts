export const QUALITY_RANK: Record<string, number> = {
  hires: 7,
  hi_res: 7,
  lossless: 6,
  sq: 5,
  highest: 4,
  higher: 3,
  standard: 2,
  normal: 2,
  lowest: 1,
};

export const FALLBACK_TARGET_VERSIONS = [
  "lossless/flac",
  "hi_res/aac",
  "spatial/aac",
  "highest/aac",
  "higher/aac",
  "medium/aac",
];

export const EXPORT_FORMAT_LABELS: Record<string, string> = {
  playable: "普通音频文件",
  mp3: "MP3",
  flac: "FLAC",
};

export const EXPORT_FORMAT_DESCRIPTIONS: Record<string, string> = {
  playable: "解密后导出为通用可播放音频，兼容性最好",
  mp3: "转码为 MP3 格式，需安装 ffmpeg",
  flac: "转码为 FLAC 格式，需安装 ffmpeg",
};

export const ALLOWED_FORMATS = ["playable", "mp3", "flac"] as const;

export const DEFAULT_MP3_BITRATE = 192;

export function normalizeText(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

export function compareText(a: string, b: string): number {
  return String(a || "").localeCompare(String(b || ""), "zh-CN", {
    numeric: true,
    sensitivity: "base",
  });
}

export function qualityScore(quality?: string | null): number {
  const normalized = normalizeText(quality).replaceAll("-", "_");
  return QUALITY_RANK[normalized] || 0;
}

export function versionScore(value?: string | null): number {
  const [quality, codec = ""] = String(value || "").split("/");
  const normalizedQuality = normalizeText(quality).replaceAll("-", "_");
  const codecBoost = normalizeText(codec) === "flac" ? 0.5 : 0;
  return (QUALITY_RANK[normalizedQuality] || 0) + codecBoost;
}

export function sortedTargetVersions(values: string[]): string[] {
  return Array.from(new Set(values.map(normalizeText).filter(Boolean))).sort(
    (left, right) =>
      versionScore(right) - versionScore(left) || compareText(left, right)
  );
}

export function targetVersionLabel(value: string): string {
  const [quality, codec, extension] = String(value || "").split("/");
  if (!quality) return "";
  return [quality, codec, extension].filter(Boolean).join(" / ");
}

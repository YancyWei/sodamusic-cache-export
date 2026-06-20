export function formatBitrate(value?: number | null): string {
  if (!value) return "—";
  return `${Math.round(value / 1000)} kbps`;
}

export function formatBytes(value?: number | null): string {
  if (!value) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let size = Number(value);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const rounded = size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1);
  return `${rounded} ${units[unit]}`;
}

export function formatDuration(value?: number | null): string {
  if (!value) return "";
  const seconds = Math.round(value / 1000);
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function formatElapsed(seconds: number): string {
  if (!seconds || seconds < 0) return "0s";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${minutes}m ${rest}s`;
}

export function formatPercent(current: number, total: number): string {
  if (!total) return "0%";
  return `${Math.min(100, Math.round((current / total) * 100))}%`;
}

export function escapeHtml(value: unknown): string {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

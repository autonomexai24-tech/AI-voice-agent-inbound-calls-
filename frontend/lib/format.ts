export function formatDateTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatDuration(seconds?: number) {
  const safe = Math.max(0, Number(seconds || 0));
  if (safe < 60) return `${safe}s`;
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  return `${minutes}m ${rest}s`;
}

export function maskPhone(value?: string) {
  if (!value) return "Unknown";
  const cleaned = value.replace(/\s+/g, "");
  if (cleaned.length <= 4) return cleaned;
  return `${cleaned.slice(0, 3)}****${cleaned.slice(-4)}`;
}

export function titleCase(value?: string) {
  if (!value) return "Unknown";
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\w\S*/g, (word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase());
}

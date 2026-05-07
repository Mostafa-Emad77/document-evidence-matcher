/** Human-readable label for server-reported parse duration (seconds). */
export function formatParseDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return null
  const s = Number(seconds)
  if (s < 60) {
    const t = Number(s.toFixed(2))
    return `${t} s`
  }
  const m = Math.floor(s / 60)
  const rem = Math.round(s - m * 60)
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`
}

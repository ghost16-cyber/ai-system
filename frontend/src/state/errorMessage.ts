/** Backend errors are typed detail dicts (e.g. `{"detail": {"code": "...", "message": "..."}}`)
 * serialized as the raw response body. Surfacing that raw JSON to the user instead of the
 * human-readable message is a display bug, not an authority decision -- this only reformats
 * text that already reached the client. */
export function describeAstraError(rawMessage: string): string {
  const trimmed = rawMessage.trim();
  if (!trimmed.startsWith("{")) return rawMessage;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (!parsed || typeof parsed !== "object") return rawMessage;
    const detail = (parsed as Record<string, unknown>).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
      const message = (detail as Record<string, unknown>).message;
      if (typeof message === "string" && message.trim()) return message;
    }
    return rawMessage;
  } catch {
    return rawMessage;
  }
}

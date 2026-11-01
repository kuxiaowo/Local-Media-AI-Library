export type SettingsValues = Record<string, string | number | boolean>;

export function localDateBoundary(value: string, nextDay = false): number {
  const [year, month, day] = value.split("-").map(Number);
  // A date-only ISO string is UTC. Construct local calendar midnight instead;
  // advancing the calendar day also handles 23/25-hour daylight-saving days.
  return new Date(year, month - 1, day + (nextDay ? 1 : 0)).getTime() / 1000;
}

export function settingsPatch(form: SettingsValues, saved: SettingsValues): SettingsValues {
  const body: SettingsValues = {};
  for (const [key, value] of Object.entries(form)) {
    if (key.endsWith("_configured") || key === "active_profile") continue;
    if ((key === "api_key" || key === "embedding_key") && !value) continue;
    if (!Object.is(value, saved[key])) body[key] = value;
  }
  return body;
}

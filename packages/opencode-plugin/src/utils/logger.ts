/**
 * Plugin logging utility
 */

export function log(
  debug: boolean,
  message: string,
  data?: Record<string, unknown>
): void {
  if (!debug) return;

  const timestamp = new Date().toISOString();
  const dataStr = data ? ` ${JSON.stringify(data)}` : "";
  console.error(`[sugar-plugin ${timestamp}] ${message}${dataStr}`);
}

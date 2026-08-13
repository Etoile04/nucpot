/** Format an ISO timestamp for display in the admin UI.
 *
 * Used across hub components (NodeDetailDrawer, HubAdminContent,
 * NodeSyncStats) to avoid triple-duplicating this function.
 */
export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—"
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? iso : new Date(ms).toLocaleString("zh-CN")
}

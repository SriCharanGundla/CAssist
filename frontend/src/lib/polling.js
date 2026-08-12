export function adaptivePollingInterval(query, terminalStatuses) {
  if (query.state.data && terminalStatuses.has(query.state.data.status)) {
    return false
  }
  const completedPolls = query.state.dataUpdateCount || 0
  if (completedPolls < 3) return 2_000
  if (completedPolls < 6) return 5_000
  return 10_000
}

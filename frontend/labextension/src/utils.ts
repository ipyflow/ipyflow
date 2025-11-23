export function mergeMaps<V>(
  priority: { [id: string]: V },
  backup: { [id: string]: V }
): { [id: string]: V } {
  const merged: { [id: string]: V } = {};
  for (const key in backup) {
    merged[key] = backup[key];
  }
  for (const key in priority) {
    merged[key] = priority[key];
  }
  return merged;
}

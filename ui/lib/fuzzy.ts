// Subsequence fuzzy matcher. Returns a score, or null when the query's
// characters cannot appear in order inside the haystack. Higher score = better
// match. Exact substring hits dominate; word-boundary and consecutive
// character matches are weighted above plain subsequence hits.

export function fuzzyScore(haystack: string, needle: string): number | null {
  if (!needle) return 0;
  const hay = haystack.toLowerCase();
  const need = needle.toLowerCase();

  const exactIdx = hay.indexOf(need);
  if (exactIdx !== -1) {
    const prefixBonus = exactIdx === 0 ? 500 : 0;
    return 1000 - exactIdx + prefixBonus;
  }

  let hayIdx = 0;
  let score = 0;
  let prevMatchedAt = -2;
  for (let i = 0; i < need.length; i++) {
    const ch = need[i];
    let found = -1;
    for (let j = hayIdx; j < hay.length; j++) {
      if (hay[j] === ch) {
        found = j;
        break;
      }
    }
    if (found === -1) return null;
    if (found === prevMatchedAt + 1) score += 5;
    else if (found === 0) score += 4;
    else if (/[\s\-_/.]/.test(hay[found - 1] ?? "")) score += 3;
    else score += 1;
    prevMatchedAt = found;
    hayIdx = found + 1;
  }
  return score;
}

export interface FuzzyTaskLike {
  id: string;
  title: string;
  description: string | null;
  tags: string[];
}

// Score a task against the query by probing each searchable field and
// returning the best score. Null means no field matched.
export function matchTask(task: FuzzyTaskLike, query: string): number | null {
  if (!query.trim()) return 0;
  const fields: string[] = [task.title, task.id, ...task.tags];
  if (task.description) fields.push(task.description);
  let best: number | null = null;
  for (const f of fields) {
    const s = fuzzyScore(f, query);
    if (s !== null && (best === null || s > best)) best = s;
  }
  return best;
}

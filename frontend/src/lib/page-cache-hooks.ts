import { useLayoutEffect, useRef, useState } from "react";

export function useVisitedPageKeys<T extends string>(activeKey: T) {
  const [visited, setVisited] = useState<Set<T>>(() => new Set([activeKey]));
  if (!visited.has(activeKey)) {
    const next = new Set(visited);
    next.add(activeKey);
    setVisited(next);
    return next;
  }
  return visited;
}

export function usePageScrollCache(activeKey: string) {
  const activeRef = useRef(activeKey);
  const positionsRef = useRef(new Map<string, number>());

  useLayoutEffect(() => {
    const previousKey = activeRef.current;
    if (previousKey === activeKey) return;
    positionsRef.current.set(previousKey, window.scrollY);
    activeRef.current = activeKey;
    window.scrollTo({ top: positionsRef.current.get(activeKey) || 0, behavior: "auto" });
  }, [activeKey]);
}

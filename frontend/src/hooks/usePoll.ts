import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Run `load` now and every `intervalMs` while `active(data)` is true; stops on unmount and starts
 * over when `deps` change (contract §14: the one polling hook of the console). A failed load keeps
 * the last data, reports the error and retries at twice the interval. `refresh()` loads at once
 * and resumes polling if `active` allows it. Answers of an earlier `deps` generation are dropped.
 */
export function usePoll<T>(load: () => Promise<T>, intervalMs: number, active: (data: T | undefined) => boolean,
                           deps: unknown[] = []): { data: T | undefined; error: Error | undefined; refresh: () => void } {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const timer = useRef<number>();
  const generation = useRef(0);
  const latest = useRef({ load, active, intervalMs });
  latest.current = { load, active, intervalMs };

  const tick = useCallback(async (gen: number) => {
    window.clearTimeout(timer.current);
    const { load: run, active: keepGoing, intervalMs: every } = latest.current;
    try {
      const next = await run();
      if (gen !== generation.current) return;
      setData(next);
      setError(undefined);
      if (keepGoing(next)) timer.current = window.setTimeout(() => void tick(gen), every);
    } catch (e) {
      if (gen !== generation.current) return;
      setError(e instanceof Error ? e : new Error(String(e)));
      timer.current = window.setTimeout(() => void tick(gen), every * 2);
    }
  }, []);

  useEffect(() => {
    const gen = ++generation.current;
    void tick(gen);
    return () => { generation.current++; window.clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const refresh = useCallback(() => void tick(generation.current), [tick]);
  return { data, error, refresh };
}

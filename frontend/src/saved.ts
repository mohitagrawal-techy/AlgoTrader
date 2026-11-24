/**
 * Copies of server data kept in this browser's local storage, so a page can show the last
 * data at once on reload, and keep showing it while the server is unreachable.
 */
const MAX_SAVED_CHARS = 1_500_000;

export interface SavedStore {
  read<T>(key: string): T | null;
  write(key: string, value: unknown): void;
}

export function savedStore(prefix: string): SavedStore {
  return {
    read<T>(key: string): T | null {
      try {
        const raw = localStorage.getItem(prefix + key);
        return raw ? (JSON.parse(raw) as T) : null;
      } catch {
        return null;
      }
    },
    write(key: string, value: unknown): void {
      try {
        const text = JSON.stringify(value);
        if (text.length <= MAX_SAVED_CHARS) localStorage.setItem(prefix + key, text);
      } catch {
        // Storage full or blocked: the page still works, it just starts empty next time.
      }
    },
  };
}

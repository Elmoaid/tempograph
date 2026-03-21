import { vi, beforeEach } from "vitest";

// Node 22+ exposes a built-in localStorage without .clear().
// Override with a proper in-memory implementation for tests.
const makeStorage = () => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = String(value); },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
};

const storage = makeStorage();
vi.stubGlobal("localStorage", storage);

beforeEach(() => {
  storage.clear();
});

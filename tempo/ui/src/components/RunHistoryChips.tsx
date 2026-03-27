import type { RunHistoryEntry } from "./useModeRunner";

interface RunHistoryChipsProps {
  runHistory: RunHistoryEntry[];
  onRunHistoryEntry: (entry: RunHistoryEntry) => void;
}

export function RunHistoryChips({ runHistory, onRunHistoryEntry }: RunHistoryChipsProps) {
  if (runHistory.length === 0) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
      {runHistory.map((entry, i) => {
        const truncArgs = entry.args.length > 20 ? entry.args.slice(0, 20) + "…" : entry.args;
        const label = truncArgs ? `${entry.mode} ${truncArgs}` : entry.mode;
        return (
          <button
            key={i}
            className="btn btn-ghost"
            onClick={() => onRunHistoryEntry(entry)}
            title={entry.args ? `${entry.mode} ${entry.args}` : entry.mode}
            style={{ fontSize: 10, padding: "2px 7px", borderRadius: 10, opacity: 0.75 }}
          >
            ⟳ {label}
          </button>
        );
      })}
    </div>
  );
}

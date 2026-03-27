import { type RefObject } from "react";
import { X } from "lucide-react";

interface OutputFilterBarProps {
  filterInputRef: RefObject<HTMLInputElement>;
  value: string;
  matchCount: number | null;
  onChange: (v: string) => void;
  onClose: () => void;
}

export function OutputFilterBar({ filterInputRef, value, matchCount, onChange, onClose }: OutputFilterBarProps) {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center", marginBottom: 4 }}>
      <input
        ref={filterInputRef}
        className="input"
        placeholder="Filter lines…"
        aria-label="Filter output lines"
        value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => { if (e.key === "Escape") onClose(); }}
        style={{ flex: 1, fontSize: 10, padding: "2px 6px" }}
      />
      {matchCount !== null && (
        <span style={{ fontSize: 9, color: "var(--text-tertiary)", whiteSpace: "nowrap" }} aria-live="polite" aria-atomic="true">
          {matchCount} lines
        </span>
      )}
      <button className="btn btn-ghost" onClick={onClose} style={{ padding: "2px 4px" }} aria-label="Close filter">
        <X size={9} aria-hidden="true" />
      </button>
    </div>
  );
}

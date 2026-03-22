import type { RefObject } from "react";
import { X } from "lucide-react";

interface OutputSearchBarProps {
  searchText: string;
  matchCount: number;
  currentMatch: number;
  searchInputRef: RefObject<HTMLInputElement>;
  onChange: (text: string) => void;
  onNavigate: (dir: "next" | "prev") => void;
  onClose: () => void;
}

export function OutputSearchBar({
  searchText,
  matchCount,
  currentMatch,
  searchInputRef,
  onChange,
  onNavigate,
  onClose,
}: OutputSearchBarProps) {
  return (
    <div
      style={{
        display: "flex",
        gap: 4,
        alignItems: "center",
        padding: "3px 6px",
        borderBottom: "1px solid var(--border-subtle)",
        background: "var(--bg-secondary)",
      }}
    >
      <input
        ref={searchInputRef}
        className="input"
        placeholder="Find in output…"
        aria-label="Search output"
        value={searchText}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => {
          if (e.key === "Escape") { onClose(); return; }
          if (e.key === "Enter") {
            e.preventDefault();
            onNavigate(e.shiftKey ? "prev" : "next");
          }
        }}
        style={{ flex: 1, fontSize: 10, padding: "2px 6px" }}
      />
      <span
        style={{ fontSize: 9, color: "var(--text-tertiary)", whiteSpace: "nowrap", minWidth: 48, textAlign: "right" }}
        aria-live="polite"
        aria-atomic="true"
      >
        {searchText.trim()
          ? matchCount === 0
            ? "No matches"
            : `${currentMatch} / ${matchCount}`
          : ""}
      </span>
      <button
        className="btn btn-ghost"
        onClick={() => onNavigate("prev")}
        disabled={matchCount === 0}
        title="Previous match (Shift+Enter)"
        aria-label="Previous match"
        style={{ padding: "2px 5px", fontSize: 9, opacity: matchCount === 0 ? 0.3 : 1 }}
      >
        ↑
      </button>
      <button
        className="btn btn-ghost"
        onClick={() => onNavigate("next")}
        disabled={matchCount === 0}
        title="Next match (Enter)"
        aria-label="Next match"
        style={{ padding: "2px 5px", fontSize: 9, opacity: matchCount === 0 ? 0.3 : 1 }}
      >
        ↓
      </button>
      <button
        className="btn btn-ghost"
        onClick={onClose}
        style={{ padding: "2px 4px" }}
        aria-label="Close search"
        title="Close (Escape)"
      >
        <X size={9} aria-hidden="true" />
      </button>
    </div>
  );
}

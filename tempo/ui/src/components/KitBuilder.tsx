import { useState, useEffect, useRef } from "react";
import { MODES } from "./modes";
import { readFile, writeFile } from "./tempo";

interface KitSpec {
  steps: string[];
  description: string;
  needsQuery: boolean;
}

type KitsJson = Record<string, KitSpec>;

interface Props {
  repoPath: string;
  onSave: (kitId: string) => void;
  onClose: () => void;
}

function toKitId(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "custom_kit";
}

export function KitBuilder({ repoPath, onSave, onClose }: Props) {
  const [name, setName] = useState("");
  const [selectedModes, setSelectedModes] = useState<Set<string>>(new Set());
  const [needsQuery, setNeedsQuery] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const nameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameInputRef.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const toggleMode = (mode: string) => {
    setSelectedModes(prev => {
      const next = new Set(prev);
      if (next.has(mode)) next.delete(mode); else next.add(mode);
      return next;
    });
  };

  const kitId = toKitId(name);
  const canSave = name.trim().length > 0 && selectedModes.size > 0;

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setError("");
    try {
      // Read existing kits.json (may not exist yet)
      let existing: KitsJson = {};
      const r = await readFile(`${repoPath}/.tempo/kits.json`);
      if (r.success && r.output) {
        try { existing = JSON.parse(r.output); } catch { /* ignore malformed */ }
      }

      const steps = MODES.filter(m => selectedModes.has(m.mode)).map(m => m.mode);
      const description = name.trim() || `Custom kit: ${steps.join(" + ")}`;

      const updated: KitsJson = {
        ...existing,
        [kitId]: { steps, description, needsQuery },
      };

      const result = await writeFile(
        `${repoPath}/.tempo/kits.json`,
        JSON.stringify(updated, null, 2)
      );

      if (!result.success) throw new Error(result.output || "Write failed");
      onSave(kitId);
    } catch (e) {
      setError(String(e));
    }
    setSaving(false);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Create Kit"
      style={{
        position: "fixed", inset: 0, zIndex: 9000,
        background: "rgba(0,0,0,0.65)", backdropFilter: "blur(2px)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        background: "var(--bg-secondary)", border: "1px solid var(--border)",
        borderRadius: 8, width: 420, maxHeight: "80vh",
        display: "flex", flexDirection: "column", overflow: "hidden",
        boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
      }}>
        {/* Header */}
        <div style={{
          padding: "12px 16px", borderBottom: "1px solid var(--border-subtle)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)" }}>
            Create Kit
          </span>
          <button
            onClick={onClose}
            style={{
              background: "none", border: "none", cursor: "pointer",
              color: "var(--text-tertiary)", fontSize: 16, lineHeight: 1, padding: 2,
            }}
            aria-label="Close"
          >×</button>
        </div>

        {/* Body */}
        <div style={{ padding: "14px 16px", overflowY: "auto", flex: 1, display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Name */}
          <div>
            <label style={{ display: "block", fontSize: 11, fontWeight: 600, color: "var(--text-tertiary)", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.4px" }}>
              Kit Name
            </label>
            <input
              ref={nameInputRef}
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && canSave) handleSave(); }}
              placeholder="e.g. Quick Review"
              style={{
                width: "100%", boxSizing: "border-box",
                background: "var(--bg-primary)", border: "1px solid var(--border)",
                borderRadius: 4, padding: "7px 10px", fontSize: 12,
                color: "var(--text-primary)", outline: "none",
              }}
            />
            {name.trim() && (
              <span style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 3, display: "block" }}>
                ID: {kitId}
              </span>
            )}
          </div>

          {/* Mode checklist */}
          <div>
            <label style={{ display: "block", fontSize: 11, fontWeight: 600, color: "var(--text-tertiary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.4px" }}>
              Modes ({selectedModes.size} selected)
            </label>
            <div style={{
              border: "1px solid var(--border-subtle)", borderRadius: 4,
              maxHeight: 220, overflowY: "auto",
            }}>
              {MODES.map((m, i) => (
                <label
                  key={m.mode}
                  style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "7px 10px", cursor: "pointer",
                    background: selectedModes.has(m.mode) ? "var(--bg-tertiary)" : "transparent",
                    borderTop: i > 0 ? "1px solid var(--border-subtle)" : "none",
                    transition: "background 0.1s",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedModes.has(m.mode)}
                    onChange={() => toggleMode(m.mode)}
                    style={{ accentColor: "var(--accent)", flexShrink: 0 }}
                  />
                  <m.icon size={12} />
                  <span style={{ fontSize: 12, color: "var(--text-primary)", flex: 1 }}>{m.label}</span>
                  <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{m.tag}</span>
                </label>
              ))}
            </div>
          </div>

          {/* needsQuery toggle */}
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={needsQuery}
              onChange={e => setNeedsQuery(e.target.checked)}
              style={{ accentColor: "var(--accent)" }}
            />
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Requires a query (symbol / task description)</span>
          </label>

          {error && (
            <span style={{ fontSize: 11, color: "var(--error, #f87171)" }}>{error}</span>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: "10px 16px", borderTop: "1px solid var(--border-subtle)",
          display: "flex", justifyContent: "flex-end", gap: 8,
        }}>
          <button
            onClick={onClose}
            style={{
              background: "none", border: "1px solid var(--border)", borderRadius: 4,
              padding: "6px 14px", fontSize: 12, color: "var(--text-secondary)", cursor: "pointer",
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!canSave || saving}
            style={{
              background: canSave && !saving ? "var(--accent)" : "var(--bg-tertiary)",
              border: "none", borderRadius: 4, padding: "6px 14px",
              fontSize: 12, fontWeight: 600,
              color: canSave && !saving ? "#fff" : "var(--text-tertiary)",
              cursor: canSave && !saving ? "pointer" : "default",
              transition: "all 0.1s",
            }}
          >
            {saving ? "Saving…" : "Create Kit"}
          </button>
        </div>
      </div>
    </div>
  );
}

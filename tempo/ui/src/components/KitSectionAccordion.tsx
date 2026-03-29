import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

const EXPANDED_KEY = (activeMode: string) => `tempo-kit-expanded-${activeMode}`;

function loadExpanded(activeMode: string, modes: string[]): Set<string> {
  try {
    const raw = localStorage.getItem(EXPANDED_KEY(activeMode));
    if (raw) return new Set(JSON.parse(raw) as string[]);
  } catch { /* ignore */ }
  return new Set(modes); // default: all expanded
}

function saveExpanded(activeMode: string, expanded: Set<string>) {
  try {
    localStorage.setItem(EXPANDED_KEY(activeMode), JSON.stringify([...expanded]));
  } catch { /* ignore */ }
}

interface KitSectionAccordionProps {
  kitSections: Array<{ mode: string; content: string }>;
  activeMode: string;
  wrapEnabled: boolean;
  fontSize: number;
}

export function KitSectionAccordion({ kitSections, activeMode, wrapEnabled, fontSize }: KitSectionAccordionProps) {
  const [expandedModes, setExpandedModes] = useState<Set<string>>(
    () => loadExpanded(activeMode, kitSections.map(s => s.mode))
  );
  const [prevActiveMode, setPrevActiveMode] = useState(activeMode);

  if (prevActiveMode !== activeMode) {
    setPrevActiveMode(activeMode);
    setExpandedModes(loadExpanded(activeMode, kitSections.map(s => s.mode)));
  }

  const toggleSection = (mode: string) => {
    setExpandedModes(prev => {
      const next = new Set(prev);
      if (next.has(mode)) next.delete(mode);
      else next.add(mode);
      saveExpanded(activeMode, next);
      return next;
    });
  };

  return (
    <div
      role="region"
      aria-label="Kit mode output"
      style={{ overflow: "auto", maxHeight: "calc(100% - 64px)", display: "flex", flexDirection: "column", gap: 4 }}
    >
      {kitSections.map(({ mode, content }) => {
        const expanded = expandedModes.has(mode);
        return (
          <div key={mode} style={{ border: "1px solid var(--border)", borderRadius: 4, overflow: "hidden" }}>
            <button
              onClick={() => toggleSection(mode)}
              aria-expanded={expanded}
              style={{
                width: "100%", display: "flex", alignItems: "center", gap: 6,
                padding: "4px 8px", background: "var(--bg-secondary)",
                border: "none", cursor: "pointer", textAlign: "left",
                color: "var(--text-secondary)", fontSize: 10, fontWeight: 600,
                letterSpacing: "0.06em", textTransform: "uppercase",
                fontFamily: "var(--font-mono)",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-secondary)")}
            >
              {expanded
                ? <ChevronDown size={10} aria-hidden="true" />
                : <ChevronRight size={10} aria-hidden="true" />
              }
              {mode}
              <span style={{ marginLeft: "auto", fontSize: 8, opacity: 0.5, fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
                ~{Math.round(content.length / 4).toLocaleString()} tok
              </span>
            </button>
            {expanded && (
              <pre className="output" style={{ margin: 0, borderRadius: 0, maxHeight: 300, overflow: "auto", whiteSpace: wrapEnabled ? "pre-wrap" : "pre", fontSize }}>
                {content}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );
}

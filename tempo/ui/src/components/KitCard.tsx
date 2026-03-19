import type { KitInfo } from "./kits";

interface KitCardProps {
  kit: KitInfo;
  active: boolean;
  cached: boolean;
  onClick: (id: string) => void;
}

export function KitCard({ kit, active, cached, onClick }: KitCardProps) {
  return (
    <button
      role="option"
      aria-selected={active}
      aria-label={`${kit.label}${kit.needsQuery ? " (requires query)" : ""}${cached ? " — cached" : ""}`}
      onClick={() => onClick(kit.id)}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "8px 10px",
        borderRadius: 6,
        cursor: "pointer",
        transition: "background 0.1s",
        border: "none",
        width: "100%",
        textAlign: "left",
        background: active ? "var(--accent-muted)" : "none",
        color: active ? "var(--accent-hover)" : "var(--text-secondary)",
      }}
      onMouseEnter={(e) => {
        if (!active) (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-hover)";
        if (!active) (e.currentTarget as HTMLButtonElement).style.color = "var(--text-primary)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = active ? "var(--accent-muted)" : "none";
        (e.currentTarget as HTMLButtonElement).style.color = active ? "var(--accent-hover)" : "var(--text-secondary)";
      }}
    >
      <span aria-hidden="true" style={{
        marginTop: 1,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        color: active ? "var(--accent-hover)" : "var(--text-tertiary)",
      }}>
        <kit.icon size={14} />
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 2,
        }}>
          <span style={{
            fontWeight: 600,
            fontSize: 12,
            color: active ? "var(--accent-hover)" : "var(--text-primary)",
          }}>
            {kit.label}
          </span>
          {kit.needsQuery && (
            <span aria-hidden="true" style={{
              fontSize: 9,
              padding: "1px 4px",
              borderRadius: 3,
              background: "var(--bg-tertiary)",
              color: "var(--text-tertiary)",
              textTransform: "uppercase",
              letterSpacing: "0.3px",
            }}>
              query
            </span>
          )}
          {cached && (
            <span aria-hidden="true" title="Has cached output" style={{
              width: 5,
              height: 5,
              borderRadius: "50%",
              background: active ? "var(--accent-hover)" : "var(--success)",
              flexShrink: 0,
              opacity: active ? 0.7 : 1,
              marginLeft: "auto",
            }} />
          )}
        </span>
        <span style={{
          display: "block",
          fontSize: 11,
          color: active ? "var(--accent)" : "var(--text-tertiary)",
          lineHeight: 1.4,
          whiteSpace: "normal",
        }}>
          {kit.description}
        </span>
      </span>
    </button>
  );
}

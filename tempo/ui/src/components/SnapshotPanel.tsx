import { useEffect, useState } from "react";
import { Package, CheckCircle, Circle, X } from "lucide-react";
import { pathExists } from "./tempo";

interface SnapshotEntry {
  slug: string;
  label: string;
  lang: string;
}

const SNAPSHOTS: SnapshotEntry[] = [
  { slug: "pallets/flask", label: "Flask", lang: "Python" },
  { slug: "django/django", label: "Django", lang: "Python" },
  { slug: "encode/httpx", label: "httpx", lang: "Python" },
  { slug: "expressjs/express", label: "Express", lang: "JS" },
  { slug: "tiangolo/fastapi", label: "FastAPI", lang: "Python" },
];

interface SnapshotPanelProps {
  homeDir: string;
  onLoad: (path: string) => void;
  onClose: () => void;
}

export function SnapshotPanel({ homeDir, onLoad, onClose }: SnapshotPanelProps) {
  const [downloaded, setDownloaded] = useState<Record<string, boolean | null>>(() =>
    Object.fromEntries(SNAPSHOTS.map((s) => [s.slug, null]))
  );

  useEffect(() => {
    if (!homeDir) return;
    const check = async () => {
      const results: Record<string, boolean> = {};
      for (const { slug } of SNAPSHOTS) {
        const [org, repo] = slug.split("/");
        const dbPath = `${homeDir}/.tempograph/snapshots/${org}/${repo}/.tempograph/graph.db`;
        results[slug] = await pathExists(dbPath);
      }
      setDownloaded(results);
    };
    check();
  }, [homeDir]);

  return (
    <div style={{
      borderBottom: "1px solid var(--border)",
      background: "var(--bg-secondary)",
      padding: "6px 12px 8px",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Package size={11} color="var(--accent)" />
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
          Pre-indexed Snapshots
        </span>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)", marginLeft: 4 }}>
          Load without indexing
        </span>
        <button
          className="btn-ghost"
          onClick={onClose}
          style={{ marginLeft: "auto", padding: "2px 4px", lineHeight: 1 }}
          title="Close snapshots"
        >
          <X size={12} />
        </button>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {SNAPSHOTS.map(({ slug, label, lang }) => {
          const [org, repo] = slug.split("/");
          const loadPath = `${homeDir}/.tempograph/snapshots/${org}/${repo}`;
          const isReady = downloaded[slug];

          return (
            <div
              key={slug}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "4px 8px",
                borderRadius: 4,
                border: "1px solid var(--border)",
                background: "var(--bg-primary)",
                fontSize: 11,
              }}
            >
              {isReady === null && <Circle size={10} color="var(--text-tertiary)" />}
              {isReady === true && <CheckCircle size={10} color="var(--accent)" />}
              {isReady === false && <Circle size={10} color="var(--text-tertiary)" />}
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{label}</span>
              <span style={{ color: "var(--text-tertiary)", fontSize: 10 }}>{lang}</span>
              {isReady === false && (
                <span style={{ color: "var(--text-tertiary)", fontSize: 10, fontStyle: "italic" }}>not downloaded</span>
              )}
              <button
                className="btn"
                onClick={() => onLoad(loadPath)}
                style={{ padding: "1px 7px", fontSize: 10, marginLeft: 2 }}
                title={`Load ${slug} as workspace (run: python3 -m tempograph snapshot --repo ${slug} to download first)`}
              >
                Load
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

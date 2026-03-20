import { useEffect, useState } from "react";
import { Package, CheckCircle, Circle, X, Loader2 } from "lucide-react";
import { pathExists, downloadSnapshot } from "./tempo";

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
  const [downloading, setDownloading] = useState<Record<string, boolean>>({});
  const [downloadError, setDownloadError] = useState<Record<string, string>>({});

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

  const handleDownload = async (slug: string) => {
    setDownloading((prev) => ({ ...prev, [slug]: true }));
    setDownloadError((prev) => ({ ...prev, [slug]: "" }));
    const result = await downloadSnapshot(slug);
    setDownloading((prev) => ({ ...prev, [slug]: false }));
    if (result.success) {
      const [org, repo] = slug.split("/");
      const dbPath = `${homeDir}/.tempograph/snapshots/${org}/${repo}/.tempograph/graph.db`;
      const exists = await pathExists(dbPath);
      setDownloaded((prev) => ({ ...prev, [slug]: exists }));
    } else {
      const msg = (result.output || "Download failed").slice(0, 60);
      setDownloadError((prev) => ({ ...prev, [slug]: msg }));
    }
  };

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
          const isDownloading = downloading[slug];
          const error = downloadError[slug];

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
                flexWrap: "wrap",
              }}
            >
              {isReady === null && <Circle size={10} color="var(--text-tertiary)" />}
              {isReady === true && <CheckCircle size={10} color="var(--accent)" />}
              {isReady === false && !isDownloading && <Circle size={10} color="var(--text-tertiary)" />}
              {isDownloading && (
                <span style={{ display: "flex", animation: "spin 1s linear infinite" }}>
                  <Loader2 size={10} color="var(--accent)" />
                </span>
              )}
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{label}</span>
              <span style={{ color: "var(--text-tertiary)", fontSize: 10 }}>{lang}</span>
              {isReady === false && !isDownloading && (
                <button
                  className="btn-ghost"
                  onClick={() => handleDownload(slug)}
                  style={{ padding: "1px 6px", fontSize: 10, marginLeft: 2 }}
                  title={`Download ${slug}`}
                >
                  Download
                </button>
              )}
              {isDownloading && (
                <span style={{ color: "var(--text-tertiary)", fontSize: 10, fontStyle: "italic" }}>
                  downloading…
                </span>
              )}
              {error && !isDownloading && (
                <span style={{ color: "var(--warning, #e5a430)", fontSize: 10 }} title={error}>
                  error
                </span>
              )}
              <button
                className="btn"
                onClick={() => onLoad(loadPath)}
                disabled={isReady === false || isDownloading}
                style={{ padding: "1px 7px", fontSize: 10, marginLeft: 2, opacity: isReady === false ? 0.4 : 1 }}
                title={isReady === false ? `Download first` : `Load ${slug} as workspace`}
              >
                Load
              </button>
            </div>
          );
        })}
      </div>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

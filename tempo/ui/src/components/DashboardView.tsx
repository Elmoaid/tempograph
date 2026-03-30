import { useState, useEffect, useRef } from "react";
import { runTempo } from "./tempo";

// --- parse helpers (exported for tests) ---

export interface DashboardStats {
  files: number;
  symbols: number;
  edges: number;
  lines: string;
}

export interface HotspotEntry {
  rank: number;
  name: string;
  risk: number;
  tested: boolean;
}

// eslint-disable-next-line react-refresh/only-export-components
export function parseDashboardStats(output: string): DashboardStats | null {
  const m = output.match(
    /Files:\s*([\d,]+),\s*Symbols:\s*([\d,]+),\s*Edges:\s*([\d,]+)\s*\nLines:\s*([\d,]+)/
  );
  if (!m) return null;
  const n = (s: string) => parseInt(s.replace(/,/g, ""), 10);
  return { files: n(m[1]), symbols: n(m[2]), edges: n(m[3]), lines: m[4] };
}

// eslint-disable-next-line react-refresh/only-export-components
export function parseTopHotspots(output: string, limit = 5): HotspotEntry[] {
  const entries: HotspotEntry[] = [];
  const re = /^\s*(\d+)\.\s+\S+\s+(.+?)\s+\[risk=(\d+)\](\s+\[tested\])?/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(output)) !== null && entries.length < limit) {
    entries.push({
      rank: parseInt(m[1], 10),
      name: m[2].trim(),
      risk: parseInt(m[3], 10),
      tested: Boolean(m[4]?.trim()),
    });
  }
  return entries;
}

// eslint-disable-next-line react-refresh/only-export-components
export function parseDeadPct(output: string): number | null {
  const m = output.match(/\[(\d+)%\s+of\s+\d+\s+source\s+symbols\]/);
  return m ? parseInt(m[1], 10) : null;
}

// --- sub-components ---

interface MetricCardProps {
  label: string;
  value?: number | string;
  loading: boolean;
  formatter?: (v: number) => string;
}

function MetricCard({ label, value, loading, formatter }: MetricCardProps) {
  let display: string;
  if (loading) {
    display = "—";
  } else if (value == null) {
    display = "—";
  } else if (typeof value === "number" && formatter) {
    display = formatter(value);
  } else if (typeof value === "number") {
    display = value.toLocaleString();
  } else {
    display = String(value);
  }
  return (
    <div className="dash-card">
      <div className="dash-card-value">{display}</div>
      <div className="dash-card-label">{label}</div>
    </div>
  );
}

// --- main component ---

interface Props {
  repoPath: string;
}

interface DashboardData {
  stats: DashboardStats | null;
  hotspots: HotspotEntry[];
  deadPct: number | null;
}

export function DashboardView({ repoPath }: Props) {
  const emptyData: DashboardData = { stats: null, hotspots: [], deadPct: null };
  const [data, setData] = useState<DashboardData>(emptyData);
  const [loading, setLoading] = useState(false);
  const abortRef = useRef(false);
  const [prevRepoPath, setPrevRepoPath] = useState(repoPath);

  if (prevRepoPath !== repoPath) {
    setPrevRepoPath(repoPath);
    setData(emptyData);
    if (repoPath) setLoading(true);
  }

  useEffect(() => {
    if (!repoPath) return;
    abortRef.current = false;

    Promise.all([
      runTempo(repoPath, "stats"),
      runTempo(repoPath, "hotspots"),
      runTempo(repoPath, "dead_code"),
    ])
      .then(([statsRes, hotspotsRes, deadRes]) => {
        if (abortRef.current) return;
        setData({
          stats: statsRes.success ? parseDashboardStats(statsRes.output) : null,
          hotspots: hotspotsRes.success ? parseTopHotspots(hotspotsRes.output) : [],
          deadPct: deadRes.success ? parseDeadPct(deadRes.output) : null,
        });
        setLoading(false);
      })
      .catch(() => {
        if (!abortRef.current) setLoading(false);
      });

    return () => {
      abortRef.current = true;
    };
  }, [repoPath]);

  if (!repoPath) {
    return (
      <div className="dashboard-empty">
        <div className="dashboard-empty-icon">▦</div>
        <div>Open a repository to see health metrics</div>
      </div>
    );
  }

  return (
    <div className="dashboard-view">
      <div className="dash-cards">
        <MetricCard label="Files" value={data.stats?.files} loading={loading} />
        <MetricCard label="Symbols" value={data.stats?.symbols} loading={loading} />
        <MetricCard label="Lines" value={data.stats?.lines} loading={loading} />
        <MetricCard
          label="Dead Code"
          value={data.deadPct != null ? `${data.deadPct}%` : undefined}
          loading={loading}
        />
      </div>

      {(loading || data.hotspots.length > 0) && (
        <div className="dash-hotspots">
          <div className="dash-section-title">Top Hotspots</div>
          {loading ? (
            <div className="dash-loading">Loading…</div>
          ) : (
            <ol className="dash-hotspot-list">
              {data.hotspots.map((h) => (
                <li key={h.rank} className="dash-hotspot-row">
                  <span className="dash-hotspot-rank">{h.rank}</span>
                  <span className="dash-hotspot-name" title={h.name}>
                    {h.name.length > 45 ? h.name.slice(0, 44) + "…" : h.name}
                  </span>
                  <span className="dash-hotspot-tags">
                    {h.tested && (
                      <span className="dash-tag dash-tag-green">tested</span>
                    )}
                    <span className="dash-risk">{h.risk.toLocaleString()}</span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

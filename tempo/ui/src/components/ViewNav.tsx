import { Code2, LayoutGrid, BarChart2 } from "lucide-react";

export type AppView = "modes" | "graph" | "dashboard";

interface ViewNavProps {
  activeView: AppView;
  onViewChange: (v: AppView) => void;
}

const VIEWS: { id: AppView; label: string; icon: React.ReactNode }[] = [
  { id: "modes", label: "Modes", icon: <Code2 size={12} /> },
  { id: "graph", label: "Graph", icon: <LayoutGrid size={12} /> },
  { id: "dashboard", label: "Dashboard", icon: <BarChart2 size={12} /> },
];

export function ViewNav({ activeView, onViewChange }: ViewNavProps) {
  return (
    <div className="view-nav" role="tablist" aria-label="App views">
      {VIEWS.map(({ id, label, icon }) => (
        <button
          key={id}
          role="tab"
          aria-selected={activeView === id}
          className={`view-nav-tab${activeView === id ? " active" : ""}`}
          onClick={() => onViewChange(id)}
        >
          {icon}
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}

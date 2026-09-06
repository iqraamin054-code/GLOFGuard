"use client";

import { useEffect, useMemo, useState } from "react";

type Row = Record<string, unknown>;

type SummaryCard = {
  label: string;
  value: string;
  change: string;
  tone: "cyan" | "amber" | "red" | "green";
};

function formatTimeAgo(dateString: string | unknown): string {
  if (!dateString) return "—";
  try {
    const date = new Date(String(dateString));
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffHours / 24);

    if (diffDays > 0) return `${diffDays}d ago`;
    if (diffHours > 0) return `${diffHours}h ago`;
    return "Just now";
  } catch {
    return "—";
  }
}

function getDataFreshnessColor(dateString: string | unknown): string {
  if (!dateString) return "text-slate-500";
  try {
    const date = new Date(String(dateString));
    const now = new Date();
    const diffHours = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60));

    if (diffHours < 24) return "text-emerald-400";
    if (diffHours < 72) return "text-amber-400";
    return "text-red-400";
  } catch {
    return "text-slate-500";
  }
}

const fallbackRows: Row[] = [
  {
    id: "GLOF-00001",
    lake: "PKGL-00001",
    lake_id: "PKGL-00001",
    lake_name: "Khurdopin Glacier Lake",
    latitude: 34.82899441,
    longitude: 74.06189099,
    risk_level: "Low",
    risk_score: "29.98",
    confidence_level: "Low",
    data_quality_status: "MISSING",
    observation_date: "2026-08-31",
    temperature_current: 15.8,
    baseline_susceptibility_level: "High",
  },
  {
    id: "GLOF-00002",
    lake: "PKGL-00002",
    lake_id: "PKGL-00002",
    lake_name: "Hassanabad Lake",
    latitude: 34.8200792,
    longitude: 74.08633956,
    risk_level: "Low",
    risk_score: "19.77",
    confidence_level: "Low",
    data_quality_status: "STALE",
    observation_date: "2026-08-31",
    temperature_current: 13.88,
    baseline_susceptibility_level: "High",
  },
  {
    id: "GLOF-00003",
    lake: "PKGL-00003",
    lake_id: "PKGL-00003",
    lake_name: "Passu Glacier Lake",
    latitude: 34.80645933,
    longitude: 74.07199024,
    risk_level: "Low",
    risk_score: "19.77",
    confidence_level: "Low",
    data_quality_status: "STALE",
    observation_date: "2026-08-31",
    temperature_current: 13.88,
    baseline_susceptibility_level: "High",
  },
];

const filterOptions = ["All", "High", "Moderate", "Low"] as const;

function getRiskTone(risk: string | unknown): { dot: string; ring: string; label: string } {
  const normalized = String(risk ?? "").toLowerCase();

  if (normalized === "high") {
    return { dot: "bg-red-500", ring: "ring-red-400/70", label: "text-red-200" };
  }

  if (normalized === "moderate") {
    return { dot: "bg-amber-400", ring: "ring-amber-300/70", label: "text-amber-200" };
  }

  return { dot: "bg-emerald-400", ring: "ring-emerald-300/70", label: "text-emerald-200" };
}

function toPakistanMapPoint(latitude: number | string | undefined, longitude: number | string | undefined) {
  const lat = Number(latitude ?? 0);
  const lon = Number(longitude ?? 0);

  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return { x: 50, y: 50 };
  }

  const x = ((lon - 60) / (77 - 60)) * 100;
  const y = 100 - ((lat - 23) / (38 - 23)) * 100;

  return { x: Math.min(100, Math.max(0, x)), y: Math.min(100, Math.max(0, y)) };
}

function computeRiskScoreAverage(data: Row[]): number {
  const numericScores = data
    .map((row) => Number(row.risk_score ?? 0))
    .filter((value) => Number.isFinite(value));

  if (numericScores.length === 0) return 0;
  return numericScores.reduce((sum, value) => sum + value, 0) / numericScores.length;
}

function computeRegionCards(data: Row[]) {
  if (!data || data.length === 0) {
    return [
      { name: "Northern valleys", risk: "Stable", score: "0/100", trend: "No data" },
      { name: "Central catchments", risk: "Stable", score: "0/100", trend: "No data" },
      { name: "Lower basins", risk: "Stable", score: "0/100", trend: "No data" },
    ];
  }

  const buckets = [
    { name: "Northern valleys", min: 35.8, max: 38 },
    { name: "Central catchments", min: 35.0, max: 35.8 },
    { name: "Lower basins", min: 23, max: 35.0 },
  ].map((bucket) => {
    const items = data.filter((row) => {
      const lat = Number(row.latitude ?? 0);
      return Number.isFinite(lat) && lat >= bucket.min && lat <= bucket.max;
    });

    const avg = items.length > 0 ? computeRiskScoreAverage(items) : 0;
    const risk = avg >= 70 ? "Elevated" : avg >= 45 ? "Moderate" : "Stable";

    return {
      name: bucket.name,
      risk,
      score: `${Math.round(avg)}/100`,
      trend: items.length > 0 ? `${items.length} lakes` : "No data",
    };
  });

  return buckets;
}

function computeSummaryCards(data: Row[]): SummaryCard[] {
  if (!data || data.length === 0) {
    return [
      { label: "Active alerts", value: "0", change: "No data", tone: "cyan" },
      { label: "High risk lakes", value: "0", change: "—", tone: "red" },
      { label: "Data coverage", value: "0%", change: "—", tone: "amber" },
      { label: "Average confidence", value: "—", change: "—", tone: "green" },
    ];
  }

  const highRiskCount = data.filter((r) => String(r.risk_level ?? "").toLowerCase() === "high").length;
  const hasDataCount = data.filter((r) => {
    const status = String(r.data_quality_status ?? "").toUpperCase();
    return status !== "MISSING" && status !== "";
  }).length;
  const dataPercentage = Math.round((hasDataCount / data.length) * 100);

  const confidenceValues = data
    .map((r) => {
      const conf = String(r.confidence_level ?? "").toLowerCase();
      return conf === "high" ? 1 : conf === "medium" ? 0.5 : 0;
    })
    .filter((v) => v > 0);
  const avgConfidence =
    confidenceValues.length > 0
      ? (Math.round((confidenceValues.reduce((a: number, b: number) => a + b, 0) / confidenceValues.length) * 100) + "%").replace("0%", "Low")
      : "—";

  return [
    { label: "Monitoring records", value: String(data.length), change: `${highRiskCount} high-risk`, tone: "amber" },
    { label: "High risk lakes", value: String(highRiskCount), change: "requiring focus", tone: "red" },
    { label: "Data coverage", value: `${dataPercentage}%`, change: `${hasDataCount} of ${data.length} available`, tone: "cyan" },
    {
      label: "Average confidence",
      value: avgConfidence,
      change: `${confidenceValues.length} records with level`,
      tone: "green",
    },
  ];
}

export default function Home() {
  const [rows, setRows] = useState<Row[]>(fallbackRows);
  const [status, setStatus] = useState("Loading live monitoring data...");
  const [fallbackMessage, setFallbackMessage] = useState<string | null>(null);
  const [selectedFilter, setSelectedFilter] = useState<(typeof filterOptions)[number]>("All");
  const [selectedLake, setSelectedLake] = useState<Row | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [sortBy, setSortBy] = useState<"risk_score" | "observation_date">("risk_score");

  useEffect(() => {
    let isMounted = true;

    async function loadRows() {
      setIsLoading(true);
      try {
        const response = await fetch("/api/data?table=lake_observations&limit=50");
        const payload = await response.json();

        if (!response.ok || !payload.ok) {
          throw new Error(payload.message ?? "Unable to fetch live monitoring rows.");
        }

        if (isMounted) {
          const nextRows = Array.isArray(payload.rows) && payload.rows.length > 0 ? payload.rows : fallbackRows;
          setRows(nextRows);
          setStatus(
            `Live data connected: ${payload.count ?? nextRows.length} row(s) from ${payload.table ?? "lake_observations"}.`
          );
          setFallbackMessage(null);
          setIsLoading(false);
        }
      } catch (error) {
        if (!isMounted) {
          return;
        }

        const message = error instanceof Error ? error.message : "Unknown data issue";
        setRows(fallbackRows);
        setStatus("Using regional monitoring fallback view.");
        setFallbackMessage(message);
        setIsLoading(false);
      }
    }

    loadRows();

    return () => {
      isMounted = false;
    };
  }, []);

  const filteredRows = useMemo(() => {
    let result = rows;

    // Filter by risk level
    if (selectedFilter !== "All") {
      result = result.filter((row) => {
        const value = String(row.risk_level ?? row.risk ?? "").toLowerCase();
        return value.includes(selectedFilter.toLowerCase());
      });
    }

    // Filter by search query
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter((row) => {
        const lakeId = String(row.lake_id ?? "").toLowerCase();
        const lakeName = String(row.lake_name ?? "").toLowerCase();
        return lakeId.includes(query) || lakeName.includes(query);
      });
    }

    // Sort by selected field
    result.sort((a, b) => {
      if (sortBy === "risk_score") {
        const scoreA = parseFloat(String(a.risk_score ?? 0));
        const scoreB = parseFloat(String(b.risk_score ?? 0));
        return scoreB - scoreA; // Descending (highest risk first)
      } else if (sortBy === "observation_date") {
        const dateA = new Date(String(a.observation_date ?? "")).getTime();
        const dateB = new Date(String(b.observation_date ?? "")).getTime();
        return dateB - dateA; // Descending (newest first)
      }
      return 0;
    });

    return result;
  }, [rows, selectedFilter, searchQuery, sortBy]);

  const summaryCards = useMemo(() => computeSummaryCards(rows), [rows]);
  const regionCards = useMemo(() => computeRegionCards(rows), [rows]);

  const alertList = useMemo(() => {
    if (!rows || rows.length === 0) return ["No live monitoring alerts available."];

    const highRisk = rows
      .filter((row) => String(row.risk_level ?? "").toLowerCase() === "high")
      .sort((a, b) => Number(b.risk_score ?? 0) - Number(a.risk_score ?? 0))
      .slice(0, 3);

    if (highRisk.length > 0) {
      return highRisk.map((row) => `${String(row.lake_name ?? row.lake_id ?? "Lake")}: ${String(row.risk_level ?? "High")} risk (${String(row.risk_score ?? "—")})`);
    }

    return [
      `Most recent observation: ${String(rows[0].lake_name ?? rows[0].lake_id ?? "Lake")}`,
      `Average lake score: ${Math.round(computeRiskScoreAverage(rows))}/100`,
      `Data quality: ${rows.filter((row) => String(row.data_quality_status ?? "").toUpperCase() !== "MISSING").length} records available`,
    ];
  }, [rows]);

  const currentHighRiskCount = useMemo(
    () => rows.filter((r) => String(r.risk_level ?? "").toLowerCase() === "high").length,
    [rows]
  );

  const tableColumns = useMemo(() => ["lake_id", "latitude", "longitude", "risk_level", "risk_score", "confidence_level", "data_quality_status", "observation_date"], []);

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <header className="mb-8 flex flex-col gap-4 rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-2xl shadow-slate-950/30">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.26em] text-cyan-400">GLOFGuard early alert</p>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white">Northern Pakistan glacial risk dashboard</h1>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  setIsLoading(true);
                  setTimeout(() => {
                    window.location.reload();
                  }, 300);
                }}
                disabled={isLoading}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isLoading ? "Loading..." : "↻ Refresh"}
              </button>
              <button className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-medium text-slate-950 transition hover:bg-cyan-400">
                ↓ Export
              </button>
            </div>
          </div>

          <div className="flex flex-col md:flex-row gap-3 items-start md:items-center">
            <div className="flex-1">
              <input
                type="text"
                placeholder="Search by lake name or ID..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950/50 px-3 py-2 text-sm text-white placeholder-slate-500 outline-none transition focus:border-cyan-500 focus:bg-slate-950"
              />
            </div>

            <div className="flex gap-2">
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
                className="rounded-lg border border-slate-700 bg-slate-950/50 px-3 py-2 text-sm text-slate-200 outline-none transition focus:border-cyan-500"
              >
                <option value="risk_score">Sort: Risk Score</option>
                <option value="observation_date">Sort: Latest Date</option>
              </select>
            </div>
          </div>
        </header>

        <section className="mb-8 grid gap-4 md:grid-cols-[1.6fr_0.8fr]">
          <div className="rounded-2xl border border-slate-800 bg-gradient-to-br from-slate-900 via-slate-900 to-cyan-950/60 p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-slate-400">Current conditions</p>
                <h2 className="mt-3 text-4xl font-bold text-white">{currentHighRiskCount > 0 ? "High risk" : "Stable"}</h2>
              </div>
              <div className="rounded-full border border-red-500/40 bg-red-500/10 px-3 py-1 text-xs font-medium text-red-200">
                {currentHighRiskCount} lake{currentHighRiskCount !== 1 ? "s" : ""} flagged
              </div>
            </div>

            <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {summaryCards.map((card) => (
                <div key={card.label} className="rounded-xl border border-slate-700 bg-slate-950/40 p-4">
                  <p className="text-xs uppercase tracking-[0.2em] text-slate-400">{card.label}</p>
                  <p className="mt-3 text-2xl font-semibold text-white">{card.value}</p>
                  <p
                    className={`mt-2 text-xs ${
                      card.tone === "red"
                        ? "text-red-300"
                        : card.tone === "amber"
                          ? "text-amber-300"
                          : card.tone === "cyan"
                            ? "text-cyan-300"
                            : "text-emerald-300"
                    }`}
                  >
                    {card.change}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
            <p className="text-xs uppercase tracking-[0.25em] text-slate-400">Alerts</p>
            <ul className="mt-4 space-y-3">
              {alertList.map((item) => (
                <li key={item} className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3 text-sm text-slate-200">
                  <span className="mt-1 h-2.5 w-2.5 rounded-full bg-amber-400" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mb-8 grid gap-4 md:grid-cols-3">
          {regionCards.map((region) => (
            <article key={region.name} className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-white">{region.name}</h3>
                <span className="rounded-full bg-slate-800 px-2 py-1 text-[10px] uppercase tracking-[0.2em] text-slate-300">
                  {region.risk}
                </span>
              </div>
              <p className="mt-5 text-3xl font-bold text-white">{region.score}</p>
              <p className="mt-2 text-sm text-slate-400">Trend {region.trend}</p>
            </article>
          ))}
        </section>

        <section className="mb-8 grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
            <div className="mb-4 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-slate-400">Map view</p>
                <h3 className="mt-2 text-xl font-semibold text-white">Catchment overlay</h3>
              </div>
              <div className="flex flex-wrap gap-2">
                {filterOptions.map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setSelectedFilter(option)}
                    className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                      selectedFilter === option
                        ? "bg-cyan-500 text-slate-950"
                        : "border border-slate-700 bg-slate-950 text-slate-300 hover:border-slate-500"
                    }`}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>

            <div className="relative overflow-hidden rounded-2xl border border-slate-700 bg-[radial-gradient(circle_at_20%_20%,rgba(34,211,238,0.18),transparent_20%),radial-gradient(circle_at_70%_35%,rgba(14,116,144,0.2),transparent_18%),linear-gradient(135deg,#020617,#0f172a_40%,#111827)] p-4">
              <div className="relative h-[320px] overflow-hidden rounded-xl border border-slate-700 bg-slate-900/40">
                <div className="absolute inset-0 opacity-40" style={{ backgroundImage: "linear-gradient(rgba(148,163,184,0.15) 1px, transparent 1px), linear-gradient(90deg, rgba(148,163,184,0.15) 1px, transparent 1px)", backgroundSize: "28px 28px" }} />

                <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" aria-label="Pakistan glacial lake map">
                  <path
                    d="M8,28 L14,18 L24,12 L35,10 L48,12 L56,16 L61,12 L71,14 L83,20 L92,28 L89,37 L82,41 L79,48 L84,57 L79,68 L72,75 L63,76 L58,82 L49,81 L38,85 L29,78 L18,70 L13,59 L8,49 L6,38 Z"
                    fill="rgba(15, 118, 110, 0.22)"
                    stroke="rgba(125, 211, 252, 0.45)"
                    strokeWidth="0.8"
                  />
                  <path
                    d="M18,28 L21,35 L28,38 L31,46 L27,54 L33,64 L43,69 L50,66 L59,72 L64,66 L69,60 L75,52 L70,46 L69,36 L60,32 L51,26 L42,24 L30,21 Z"
                    fill="rgba(14, 116, 144, 0.14)"
                    stroke="rgba(148, 163, 184, 0.35)"
                    strokeWidth="0.5"
                  />
                </svg>

                {filteredRows.map((row) => {
                  const point = toPakistanMapPoint(Number(row.latitude ?? 0), Number(row.longitude ?? 0));
                  const tone = getRiskTone(row.risk_level ?? row.risk);
                  const isSelected = selectedLake && String(selectedLake.lake_id ?? "") === String(row.lake_id ?? "");

                  return (
                    <button
                      type="button"
                      key={String(row.lake_id ?? `${row.latitude}-${row.longitude}`)}
                      onClick={() => setSelectedLake(row)}
                      title={`${String(row.lake_name ?? row.lake_id ?? "Lake")}: ${String(row.risk_level ?? "Unknown")} (${String(row.risk_score ?? "—")})`}
                      className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 ${tone.ring} shadow-lg transition ${isSelected ? "scale-125" : "scale-100"}`}
                      style={{
                        left: `${point.x}%`,
                        top: `${point.y}%`,
                        width: isSelected ? "18px" : "12px",
                        height: isSelected ? "18px" : "12px",
                        background: isSelected ? "rgba(255,255,255,0.9)" : undefined,
                      }}
                    >
                      <span className={`block h-full w-full rounded-full ${tone.dot}`} />
                    </button>
                  );
                })}

                <div className="absolute left-4 top-4 rounded-lg border border-slate-700 bg-slate-950/80 px-3 py-2 text-xs text-slate-300">
                  <div className="font-medium text-white">Pakistan glacier lakes</div>
                  <div className="mt-1 text-[10px] uppercase tracking-[0.2em] text-slate-400">{filteredRows.length} active points</div>
                </div>

                <div className="absolute bottom-4 right-4 rounded-lg border border-slate-700 bg-slate-950/80 px-3 py-2 text-xs text-slate-300">
                  <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-red-500" /> High</div>
                  <div className="mt-1 flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-amber-400" /> Moderate</div>
                  <div className="mt-1 flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-emerald-400" /> Low</div>
                </div>
              </div>
            </div>
          </div>

          <aside className="space-y-6">
            <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <p className="text-xs uppercase tracking-[0.25em] text-slate-400">Monitoring summary</p>
              <div className="mt-4 space-y-4">
                <div>
                  <div className="mb-2 flex items-center justify-between text-sm text-slate-300">
                    <span>Rainfall anomaly</span>
                    <span>+31%</span>
                  </div>
                  <div className="h-2.5 rounded-full bg-slate-800">
                    <div className="h-2.5 w-[78%] rounded-full bg-gradient-to-r from-amber-400 to-orange-500" />
                  </div>
                </div>

                <div>
                  <div className="mb-2 flex items-center justify-between text-sm text-slate-300">
                    <span>Lake thermal stress</span>
                    <span>+18%</span>
                  </div>
                  <div className="h-2.5 rounded-full bg-slate-800">
                    <div className="h-2.5 w-[62%] rounded-full bg-gradient-to-r from-cyan-400 to-blue-500" />
                  </div>
                </div>

                <div>
                  <div className="mb-2 flex items-center justify-between text-sm text-slate-300">
                    <span>Remote sensing coverage</span>
                    <span>87%</span>
                  </div>
                  <div className="h-2.5 rounded-full bg-slate-800">
                    <div className="h-2.5 w-[87%] rounded-full bg-gradient-to-r from-emerald-400 to-cyan-500" />
                  </div>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <p className="text-xs uppercase tracking-[0.25em] text-slate-400">System status</p>
              <div className="mt-4 rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-4 text-sm text-cyan-100">
                {status}
              </div>
              {fallbackMessage ? (
                <div className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-100">
                  {fallbackMessage}
                </div>
              ) : null}
            </div>
          </aside>
        </section>

        <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-xl font-semibold text-white">Lake watchlist</h3>
            <span className="text-xs uppercase tracking-[0.2em] text-slate-400">{filteredRows.length} record{filteredRows.length !== 1 ? "s" : ""}</span>
          </div>

          <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-700 text-left text-sm">
                <thead>
                  <tr className="text-slate-400">
                    <th className="px-3 py-3 font-medium uppercase tracking-[0.2em]">Lake Name</th>
                    <th className="px-3 py-3 font-medium uppercase tracking-[0.2em]">ID</th>
                    <th className="px-3 py-3 font-medium uppercase tracking-[0.2em]">Lat / Lon</th>
                    <th className="px-3 py-3 font-medium uppercase tracking-[0.2em]">Risk Level</th>
                    <th className="px-3 py-3 font-medium uppercase tracking-[0.2em]">Score</th>
                    <th className="px-3 py-3 font-medium uppercase tracking-[0.2em]">Confidence</th>
                    <th className="px-3 py-3 font-medium uppercase tracking-[0.2em]">Data Status</th>
                    <th className="px-3 py-3 font-medium uppercase tracking-[0.2em]">Date</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {filteredRows.map((row, index) => (
                    <tr
                      key={`${String(row.lake_id)}-${index}`}
                      onClick={() => setSelectedLake(row)}
                      className={`cursor-pointer transition ${
                        selectedLake === row ? "bg-cyan-500/20" : "hover:bg-slate-800/50"
                      }`}
                    >
                      <td className="px-3 py-3 text-slate-100 font-semibold text-sm">{String(row.lake_name ?? row.lake_id ?? "—")}</td>
                      <td className="px-3 py-3 text-slate-300 font-mono text-xs">{String(row.lake_id ?? "—")}</td>
                      <td className="px-3 py-3 text-slate-300 text-xs">
                        {row.latitude && row.longitude
                          ? `${Number(row.latitude).toFixed(2)}, ${Number(row.longitude).toFixed(2)}`
                          : "—"}
                      </td>
                      <td className="px-3 py-3">
                        <span
                          className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-medium ${
                            String(row.risk_level ?? "").toLowerCase() === "high"
                              ? "bg-red-500/10 text-red-200"
                              : String(row.risk_level ?? "").toLowerCase() === "moderate"
                                ? "bg-amber-500/10 text-amber-200"
                                : "bg-emerald-500/10 text-emerald-200"
                          }`}
                        >
                          {String(row.risk_level ?? "—")}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-slate-200 font-semibold">{String(row.risk_score ?? "—")}</td>
                      <td className="px-3 py-3 text-slate-300 text-xs">{String(row.confidence_level ?? "—")}</td>
                      <td className="px-3 py-3 text-slate-300 text-xs">
                        <span
                          className={`inline-flex rounded px-1.5 py-0.5 text-[10px] ${
                            String(row.data_quality_status ?? "").toUpperCase() === "MISSING"
                              ? "bg-slate-700/50 text-slate-300"
                              : String(row.data_quality_status ?? "").toUpperCase() === "STALE"
                                ? "bg-amber-500/10 text-amber-200"
                                : "bg-emerald-500/10 text-emerald-200"
                          }`}
                        >
                          {String(row.data_quality_status ?? "—")}
                        </span>
                      </td>
                      <td className={`px-3 py-3 text-xs font-medium ${getDataFreshnessColor(row.observation_date)}`}>
                        <span title={String(row.observation_date ?? "")}>
                          {formatTimeAgo(row.observation_date)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {selectedLake ? (
              <div className="rounded-2xl border border-slate-700 bg-gradient-to-br from-slate-900 to-slate-950 p-5 h-fit sticky top-8 space-y-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <p className="text-xs uppercase tracking-[0.2em] text-slate-500">Lake details</p>
                    <p className="mt-2 text-lg font-bold text-cyan-300">{String(selectedLake.lake_name ?? selectedLake.lake_id ?? "Unknown")}</p>
                    <p className="text-xs text-slate-500 font-mono">{String(selectedLake.lake_id ?? "—")}</p>
                  </div>
                  <button
                    onClick={() => setSelectedLake(null)}
                    className="text-slate-500 hover:text-slate-200 text-xl transition"
                  >
                    ✕
                  </button>
                </div>

                <div className="border-t border-slate-700 pt-4 space-y-3">
                  <div className="bg-slate-950/50 rounded-lg p-3">
                    <p className="text-xs uppercase tracking-[0.1em] text-slate-500">Risk Score</p>
                    <div className="mt-2 flex items-center justify-between">
                      <span className={`text-2xl font-bold ${
                        String(selectedLake.risk_level ?? "").toLowerCase() === "high"
                          ? "text-red-400"
                          : String(selectedLake.risk_level ?? "").toLowerCase() === "moderate"
                            ? "text-amber-400"
                            : "text-emerald-400"
                      }`}>
                        {String(selectedLake.risk_score ?? "—")}
                      </span>
                      <span
                        className={`text-xs font-semibold px-2 py-1 rounded ${
                          String(selectedLake.risk_level ?? "").toLowerCase() === "high"
                            ? "bg-red-500/20 text-red-200"
                            : String(selectedLake.risk_level ?? "").toLowerCase() === "moderate"
                              ? "bg-amber-500/20 text-amber-200"
                              : "bg-emerald-500/20 text-emerald-200"
                        }`}
                      >
                        {String(selectedLake.risk_level ?? "Unknown")}
                      </span>
                    </div>
                  </div>

                  <div>
                    <p className="text-xs uppercase tracking-[0.1em] text-slate-500">Location</p>
                    <p className="mt-1 text-sm text-slate-300">
                      {selectedLake.latitude && selectedLake.longitude
                        ? `${Number(selectedLake.latitude).toFixed(4)}°N, ${Number(selectedLake.longitude).toFixed(4)}°E`
                        : "No coordinates"}
                    </p>
                  </div>

                  <div>
                    <p className="text-xs uppercase tracking-[0.1em] text-slate-500">Baseline Susceptibility</p>
                    <p className="mt-1 text-sm text-slate-200">{String(selectedLake.baseline_susceptibility_level ?? "Not available")}</p>
                  </div>

                  <div>
                    <p className="text-xs uppercase tracking-[0.1em] text-slate-500">Temperature</p>
                    <p className="mt-1 text-sm text-slate-200">{selectedLake.temperature_current ? `${Number(selectedLake.temperature_current).toFixed(1)}°C` : "No data"}</p>
                  </div>

                  <div>
                    <p className="text-xs uppercase tracking-[0.1em] text-slate-500">Confidence Level</p>
                    <p className="mt-1 text-sm text-slate-200">{String(selectedLake.confidence_level ?? "Unknown")}</p>
                  </div>

                  <div>
                    <p className="text-xs uppercase tracking-[0.1em] text-slate-500">Data Quality</p>
                    <div className="mt-1 flex items-center gap-2">
                      <span
                        className={`inline-flex rounded px-2 py-1 text-xs font-medium ${
                          String(selectedLake.data_quality_status ?? "").toUpperCase() === "MISSING"
                            ? "bg-slate-700/50 text-slate-300"
                            : String(selectedLake.data_quality_status ?? "").toUpperCase() === "STALE"
                              ? "bg-amber-500/20 text-amber-200"
                              : "bg-emerald-500/20 text-emerald-200"
                        }`}
                      >
                        {String(selectedLake.data_quality_status ?? "Unknown")}
                      </span>
                      <span className={`text-xs font-medium ${getDataFreshnessColor(selectedLake.observation_date)}`}>
                        {formatTimeAgo(selectedLake.observation_date)}
                      </span>
                    </div>
                  </div>

                  <div className="border-t border-slate-700 pt-3">
                    <p className="text-xs uppercase tracking-[0.1em] text-slate-500">Last observation</p>
                    <p className="mt-1 text-xs text-slate-400">{String(selectedLake.observation_date ?? "—")}</p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-2xl border border-slate-700 bg-slate-900/40 p-4 h-fit sticky top-8 flex items-center justify-center text-center">
                <p className="text-xs text-slate-400">Click a row to view lake details</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

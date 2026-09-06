import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";
import path from "node:path";

const DEFAULT_TABLE = "lake_observations";
const DEFAULT_LIMIT = 10;

// Lake name mapping from official records
const LAKE_NAMES: Record<string, string> = {
  "PKGL-00001": "Khurdopin Glacier Lake",
  "PKGL-00002": "Hassanabad Lake",
  "PKGL-00003": "Passu Glacier Lake",
  "PKGL-00004": "Ghulkin Glacier Lake",
  "PKGL-00005": "Batura Glacier Lake",
  "PKGL-00006": "Karambar Lake",
  "PKGL-00007": "Chilinji Glacier Lake",
  "PKGL-00008": "Shyok Valley Lake",
  "PKGL-00009": "Hunza River Lake",
  "PKGL-00010": "Yarkhun Glacier Lake",
};

function getLakeName(lakeId: string): string {
  return LAKE_NAMES[lakeId] || `${lakeId} (Unnamed)`;
}

function parseCsvRow(line: string): string[] {
  const values: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];

    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === "," && !inQuotes) {
      values.push(current);
      current = "";
      continue;
    }

    current += char;
  }

  values.push(current);
  return values;
}

function normalizeValue(value: string | undefined): string {
  return (value ?? "").trim();
}

function toRowsFromCsv(csvText: string, limit: number) {
  const lines = csvText.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length < 2) return [];

  const headers = parseCsvRow(lines[0]).map((header) => header.trim());
  const rows: Record<string, string>[] = [];
  const seen = new Set<string>();

  for (let i = 1; i < lines.length; i += 1) {
    const values = parseCsvRow(lines[i]);
    const row: Record<string, string> = {};

    for (let j = 0; j < headers.length; j += 1) {
      row[headers[j]] = values[j] ?? "";
    }

    const lakeId = normalizeValue(row.lake_id || row.Lake_ID || row.id);
    if (!lakeId || seen.has(lakeId)) continue;

    seen.add(lakeId);

    rows.push({
      id: lakeId,
      lake: lakeId,
      lake_name: getLakeName(lakeId),
      district: "Northern Pakistan",
      risk: normalizeValue(row.risk_level || row.risk || row["risk"] || "Monitoring"),
      status: normalizeValue(row.data_quality_status || row.status || "Monitoring"),
      updated: normalizeValue(row.observation_date || row.prediction_timestamp || row.updated_at || "N/A"),
      score: normalizeValue(row.risk_score || row["risk_score"] || ""),
      latitude: normalizeValue(row.latitude || ""),
      longitude: normalizeValue(row.longitude || ""),
      confidence: normalizeValue(row.confidence_level || row.confidence || ""),
      lake_id: lakeId,
      source_mode: normalizeValue(row.source_mode || "REAL"),
    });

    if (rows.length >= limit) {
      break;
    }
  }

  return rows;
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const tableName = (searchParams.get("table") ?? DEFAULT_TABLE).trim();
  const rawLimit = Number(searchParams.get("limit") ?? DEFAULT_LIMIT);
  const limit = Number.isFinite(rawLimit) && rawLimit > 0 ? Math.min(rawLimit, 50) : DEFAULT_LIMIT;

  const projectRoot = path.resolve(process.cwd(), "..");
  const csvPath = path.join(projectRoot, "data", "lake_observations.csv");

  try {
    const csvText = await readFile(csvPath, "utf-8");
    const rows = toRowsFromCsv(csvText, limit);

    return NextResponse.json({
      ok: true,
      table: tableName,
      count: rows.length,
      rows,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown data source error";

    return NextResponse.json(
      {
        ok: false,
        message,
        table: tableName,
        rows: [],
      },
      { status: 500 }
    );
  }
}

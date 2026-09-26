import { NextResponse } from "next/server";
import { existsSync } from "node:fs";
import path from "node:path";

export async function GET() {
  try {
    const filePath = path.resolve(process.cwd(), "..", "data", "lake_observations.csv");
    const exists = existsSync(filePath);

    if (!exists) {
      return NextResponse.json(
        {
          ok: false,
          message: "GLOFGuard data file is missing.",
          file: filePath,
        },
        { status: 500 }
      );
    }

    return NextResponse.json({
      ok: true,
      message: "GLOFGuard live monitoring data source is available.",
      file: filePath,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown server error";
    return NextResponse.json(
      {
        ok: false,
        message,
      },
      { status: 500 }
    );
  }
}

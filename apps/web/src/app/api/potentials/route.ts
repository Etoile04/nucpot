import { NextResponse } from "next/server";
import { supabase } from "@/lib/supabase";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);

  const type = searchParams.get("type");
  const elements = searchParams.get("elements");
  const query = searchParams.get("q");
  const page = parseInt(searchParams.get("page") || "1");
  const limit = parseInt(searchParams.get("limit") || "20");
  const offset = (page - 1) * limit;

  // Sorting
  const sort = searchParams.get("sort") || "updated";
  let orderColumn = "updated_at";
  let orderAsc = false;
  if (sort === "name") {
    orderColumn = "name";
    orderAsc = true;
  } else if (sort === "type") {
    orderColumn = "type";
    orderAsc = true;
  }

  let dbQuery = supabase
    .from("potentials")
    .select("*", { count: "exact" })
    .eq("status", "published")
    .order(orderColumn, { ascending: orderAsc })
    .range(offset, offset + limit - 1);

  if (type) {
    dbQuery = dbQuery.eq("type", type);
  }

  if (elements) {
    const elemArray = elements.split(",").map((e) => e.trim());
    dbQuery = dbQuery.overlaps("elements", elemArray);
  }

  if (query) {
    // ILIKE search across name, description, and display_name
    // (replaces broken textSearch on non-existent search_vector column — NFM-1367)
    const safeQuery = query.replace(/[,().]/g, "");
    const pattern = `%${safeQuery}%`;
    dbQuery = dbQuery.or(
      `name.ilike.${pattern},description.ilike.${pattern},display_name.ilike.${pattern}`
    );
  }

  // Advanced filters
  const irradiation = searchParams.get("irradiation");
  const hasDefect = searchParams.get("hasDefect");
  const hasLiquid = searchParams.get("hasLiquid");
  const validationLevel = searchParams.get("validationLevel");
  const tempMin = searchParams.get("tempMin");
  const tempMax = searchParams.get("tempMax");

  if (irradiation === "true") {
    dbQuery = dbQuery.contains("extra", { irradiationRelevant: true });
  }
  if (hasDefect === "true") {
    dbQuery = dbQuery.contains("extra", { hasDefectData: true });
  }
  if (hasLiquid === "true") {
    dbQuery = dbQuery.contains("extra", { hasLiquidPhase: true });
  }
  if (validationLevel && validationLevel !== "all") {
    dbQuery = dbQuery.contains("extra", { validationLevel });
  }

  // Temperature range filtering via JSONB path operators
  if (tempMin) {
    const minVal = parseFloat(tempMin);
    if (!isNaN(minVal)) {
      dbQuery = dbQuery.filter(
        "applicability->temperatureRange->>1",
        "gte",
        String(minVal),
      );
    }
  }
  if (tempMax) {
    const maxVal = parseFloat(tempMax);
    if (!isNaN(maxVal)) {
      dbQuery = dbQuery.filter(
        "applicability->temperatureRange->>0",
        "lte",
        String(maxVal),
      );
    }
  }

  const { data, count, error } = await dbQuery;

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({
    potentials: data,
    total: count,
    page,
    limit,
    totalPages: Math.ceil((count || 0) / limit),
  });
}

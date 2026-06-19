import { NextResponse } from "next/server";
import { supabase } from "@/lib/supabase";

export async function GET() {
  const { count: potentialCount } = await supabase
    .from("potentials")
    .select("*", { count: "exact", head: true })
    .eq("status", "published");

  const { data: typeData } = await supabase
    .from("potentials")
    .select("type")
    .eq("status", "published");

  const { data: elementData } = await supabase
    .from("potentials")
    .select("elements")
    .eq("status", "published");

  const types = [...new Set(typeData?.map((d) => d.type) || [])];
  const elements = [...new Set(elementData?.flatMap((d) => d.elements) || [])];

  const { data: recentData } = await supabase
    .from("potentials")
    .select(
      "id, name, display_name, type, elements, system_name, updated_at",
    )
    .eq("status", "published")
    .order("updated_at", { ascending: false })
    .limit(5);

  return NextResponse.json({
    totalPotentials: potentialCount || 0,
    totalTypes: types.length,
    totalElements: elements.length,
    types,
    elements: elements.sort(),
    recent: recentData || [],
  });
}

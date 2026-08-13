import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const supabaseUrl =
  process.env.NEXT_PUBLIC_SUPABASE_URL ??
  "https://gzhiqyopzlmnkdzammhx.supabase.co";
// Cloud Supabase publishable key as fallback for serverless deployments
// where env vars may not be explicitly set.
const supabaseAnonKey =
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??
  "sb_publishable_fY3HKqTwRqPJRBOwIOdCFQ_6zJdA9iD";

export const supabase: SupabaseClient = createClient(
  supabaseUrl,
  supabaseAnonKey,
);

/** Server-side admin client (bypasses RLS). Only use in API routes. */
export const supabaseAdmin: SupabaseClient | null =
  process.env.SUPABASE_SERVICE_ROLE_KEY
    ? createClient(supabaseUrl, process.env.SUPABASE_SERVICE_ROLE_KEY)
    : null;

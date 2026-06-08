import { createClient, SupabaseClient } from '@supabase/supabase-js'

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://gzhiqyopzlmnkdzammhx.supabase.co'
// Support both legacy JWT keys (disabled) and new sb_publishable_ keys
const envKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
const supabaseAnonKey = envKey.startsWith('sb_publishable_')
  ? envKey
  : 'sb_publishable_fY3HKqTwRqPJRBOwIOdCFQ_6zJdA9iD'

export const supabase: SupabaseClient = createClient(supabaseUrl, supabaseAnonKey)

// Admin client with service_role key (bypasses RLS)
// Service role key must ONLY be available server-side (API routes / getServerSideProps)
// Never use NEXT_PUBLIC_ prefix — it would expose the key in client JS
export const supabaseAdmin: SupabaseClient | null = process.env.SUPABASE_SERVICE_ROLE_KEY
  ? createClient(supabaseUrl, process.env.SUPABASE_SERVICE_ROLE_KEY)
  : null

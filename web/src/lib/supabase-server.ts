import { createClient } from "@supabase/supabase-js";

export const supabaseDbUrl = process.env.SUPABASE_DB_URL;

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!supabaseUrl) {
  throw new Error("Missing Supabase URL. Set NEXT_PUBLIC_SUPABASE_URL or SUPABASE_URL.");
}

if (!supabaseKey) {
  throw new Error("Missing Supabase key. Set SUPABASE_SERVICE_ROLE_KEY or NEXT_PUBLIC_SUPABASE_ANON_KEY.");
}

export const supabaseServer = createClient(supabaseUrl, supabaseKey, {
  auth: {
    persistSession: false,
    autoRefreshToken: false,
    detectSessionInUrl: false,
  },
});

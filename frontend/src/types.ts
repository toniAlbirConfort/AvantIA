export type User = { id: number; username: string; role: string; active: boolean }
export type Draft = { id: number; text: string; status: string; approved_by_id: number | null; approved_at: string | null; sent_at: string | null; error: string | null }
export type Message = { id: number; direction: string; body: string; sender: string | null; subject: string | null; source_url: string | null; provider_id: string; created_at: string }
export type Audit = { action: string; actor_user_id: number | null; detail: Record<string, unknown>; created_at: string }
export type Conversation = {
  id: number; channel: string; kind: string; customer_name: string | null; customer_address: string | null;
  subject: string | null; status: string; priority: string; assigned_user_id: number | null;
  analysis: Record<string, unknown>; created_at: string; updated_at: string; last_inbound_at: string;
  draft: Draft | null; messages?: Message[]; drafts?: Draft[]; audit?: Audit[];
  booking_task?: { id: number; status: string; extracted: Record<string, unknown> } | null
}
export type Summary = { pending: number; delivery_unknown: number; bookings: number; last_import_at: string | null; last_import_rows: number }
export type ImportBatch = { id: number; filename: string; source: string; status: string; rows: number; error: string | null; created_at: string }
export type Property = { id: string; name: string; capacity: number; periods: number }

export type User = { id: number; username: string; role: string; active: boolean }
export type GowaStatus = {
  configured: boolean; service_available: boolean; device_id: string | null; registered: boolean;
  is_connected: boolean; is_logged_in: boolean; phone_number?: string | null
}
export type GowaPairing = { qr_image: string; qr_duration: number }
export type Draft = { id: number; text: string; status: string; approved_by_id: number | null; approved_at: string | null; sent_at: string | null; error: string | null }
export type ClassificationLabel = 'demand' | 'guest_service' | 'non_demand' | 'uncertain' | 'review'
export type MessageClassification = {
  label: Exclude<ClassificationLabel, 'review'> | null
  predicted_label: Exclude<ClassificationLabel, 'review'> | null
  confidence: 'high' | 'medium' | 'low' | null
  reason: string | null
  needs_review: boolean
  model: string | null
  reviewed_by_id: number | null
  reviewed_at: string | null
}
export type Message = { id: number; direction: string; body: string; html_body?: string | null; sender: string | null; recipient?: string | null; subject: string | null; source_url: string | null; provider_id: string; created_at: string; classification?: MessageClassification | null }
export type EmailTranslation = { message_id: number; language: 'es'; body: string }
export type Audit = { action: string; actor_user_id: number | null; detail: Record<string, unknown>; created_at: string }
export type Conversation = {
  id: number; channel: string; kind: string; customer_name: string | null; customer_address: string | null;
  subject: string | null; status: string; priority: string; assigned_user_id: number | null;
  active_classification: ClassificationLabel | null; classification_open: boolean;
  classification_resolved_at: string | null; classification_resolved_by_id?: number | null;
  analysis: Record<string, unknown>; created_at: string; updated_at: string; last_inbound_at: string;
  draft: Draft | null; messages?: Message[]; drafts?: Draft[]; audit?: Audit[];
  booking_task?: { id: number; status: string; extracted: Record<string, unknown> } | null
}
export type InboxThread = {
  type: 'thread'; id: number; channel: string; kind: string; customer_name: string | null;
  customer_address: string | null; subject: string | null; status: string; priority: string;
  assigned_user_id: number | null; active_classification: ClassificationLabel | null;
  classification_open: boolean; classification_resolved_at: string | null;
  classification_review_count: number; last_inbound_classification: Exclude<ClassificationLabel, 'review'> | null;
  last_inbound_needs_review: boolean; last_inbound_at: string; message_count: number;
  last_message_at: string | null; preview: string
}
export type InboxSenderGroup = {
  type: 'sender'; sender: string; display_name: string; address: string; channel: 'email';
  conversation_count: number; last_inbound_at: string
}
export type InboxItem = InboxThread | InboxSenderGroup
export type InboxResponse = { items: InboxItem[]; total: number; offset: number; limit: number }
export type Summary = { pending: number; delivery_unknown: number; bookings: number; last_import_at: string | null; last_import_rows: number }
export type ImportIssue = { code: string; locator?: string; property_id?: string; source_row?: number; source_batch_id?: number | null; locators?: string[] }
export type ImportChange = { key: string; kind: string; fields?: string[] }
export type ImportBatch = { id: number; dataset: string; filename: string; source: string; status: string; rows: number; error: string | null; created_at: string; actor_user_id: number | null; parser_version: string | null; warning_count: number; change_count: number }
export type ImportDetail = ImportBatch & { checksum: string; warnings: ImportIssue[]; changes: ImportChange[] }
export type ReservationCoverage = { first_arrival: string; last_departure: string; first_created: string | null; last_created: string | null; effective_rows?: number }
export type ImportPreview = { dataset: string; checksum: string; rows: number; effective_rows: number; carried_rows: number; duplicate_of: number | null; coverage: ReservationCoverage | null; warnings: ImportIssue[]; changes: ImportChange[] }
export type Property = { id: string; name: string; capacity: number; active: boolean; category: string; building: string | null; city: string | null; address: string | null; status_original: string; source_row: number; batch_id: number }
export type PlanningBar = { locator: string; property_id: string; status: string; status_original: string; start_date: string; end_date: string; created_on: string | null; nights: number; adults: number; children: number; infants: number; display_name: string; portal: string | null; total: string; net: string; tax: string; paid: string; pending: string }
export type PlanningData = { start: string; end: string; property_batch: { id: number; filename: string; created_at: string; rows: number } | null; reservation_batch: { id: number; filename: string; created_at: string; rows: number } | null; reservation_coverage: (ReservationCoverage & { effective_rows: number }) | null; reservations_in_range: number; properties: { id: string; name: string; capacity: number; building: string | null; active: boolean; category: string }[]; bars: PlanningBar[]; inquiries: PlanningBar[]; cancellations: PlanningBar[]; warnings: ImportIssue[] }
export type ReservationRecord = PlanningBar & { property_name: string }
export type ReservationPropertyOption = { id: string; name: string }
export type ReservationsResponse = {
  items: ReservationRecord[]
  total: number
  offset: number
  limit: number
  has_snapshot: boolean
  properties: ReservationPropertyOption[]
}

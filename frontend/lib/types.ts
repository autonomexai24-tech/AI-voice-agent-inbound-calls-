export type User = {
  email: string;
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  tenant_phone?: string;
  tenant_active?: boolean;
};

export type Stats = {
  total_calls: number;
  total_bookings: number;
  avg_duration: number;
  booking_rate: number;
};

export type CallLog = {
  id?: string;
  tenant_id?: string;
  phone_number?: string;
  caller_phone?: string;
  duration_seconds?: number;
  transcript?: string;
  summary?: string;
  sentiment?: string;
  created_at?: string;
  recording_url?: string;
  recording_id?: string;
  recording_upload_status?: string;
};

export type Booking = {
  id?: string;
  tenant_id?: string;
  call_log_id?: string;
  patient_name?: string;
  patient_phone?: string;
  name?: string;
  phone_number?: string;
  start_time?: string;
  created_at?: string;
  status?: string;
  cal_booking_uid?: string;
  summary?: string;
};

export type Config = {
  business_name?: string;
  business_phone?: string;
  tenant_slug?: string;
  tenant_active?: boolean;
  agent_instructions?: string;
  first_line?: string;
  tts_voice?: string;
  tts_language?: string;
  lang_preset?: string;
  llm_model?: string;
  stt_min_endpointing_delay?: number | string;
  business_hours_json?: string;
  transfer_number?: string;
  cal_event_type_id?: string;
  config_source?: string;
};

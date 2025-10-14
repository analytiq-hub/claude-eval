// OpenTelemetry Types

export interface TelemetrySpan {
  trace_id: string;
  span_id: string;
  parent_span_id?: string;
  name: string;
  kind?: string;
  start_time: string; // ISO datetime string
  end_time?: string; // ISO datetime string
  status?: Record<string, unknown>;
  attributes?: Record<string, unknown>;
  events?: Array<Record<string, unknown>>;
  links?: Array<Record<string, unknown>>;
}

export interface TelemetryTrace {
  resource_spans: Array<Record<string, unknown>>;
  tag_ids?: string[];
  metadata?: Record<string, string>;
}

export interface TelemetryMetric {
  name: string;
  description?: string;
  unit?: string;
  type: string;
  data_points: Array<Record<string, unknown>>;
  resource?: Record<string, unknown>;
  tag_ids?: string[];
  metadata?: Record<string, string>;
}

export interface TelemetryLog {
  timestamp: string; // ISO datetime string
  severity?: string;
  body: string;
  attributes?: Record<string, unknown>;
  resource?: Record<string, unknown>;
  trace_id?: string;
  span_id?: string;
  tag_ids?: string[];
  metadata?: Record<string, string>;
}

// Upload request types
export interface TelemetryTracesUpload {
  traces: TelemetryTrace[];
}

export interface TelemetryMetricsUpload {
  metrics: TelemetryMetric[];
}

export interface TelemetryLogsUpload {
  logs: TelemetryLog[];
}

// Response types
export interface TelemetryTraceResponse {
  trace_id: string;
  span_count: number;
  upload_date: string; // ISO datetime string
  uploaded_by: string;
  tag_ids: string[];
  metadata?: Record<string, string>;
}

export interface TelemetryMetricResponse {
  metric_id: string;
  name: string;
  type: string;
  data_point_count: number;
  upload_date: string; // ISO datetime string
  uploaded_by: string;
  tag_ids: string[];
  metadata?: Record<string, string>;
}

export interface TelemetryLogResponse {
  log_id: string;
  timestamp: string; // ISO datetime string
  severity?: string;
  body: string;
  upload_date: string; // ISO datetime string
  uploaded_by: string;
  tag_ids: string[];
  metadata?: Record<string, string>;
}

// List response types
export interface ListTelemetryTracesResponse {
  traces: TelemetryTraceResponse[];
  total: number;
  skip: number;
  limit: number;
}

export interface ListTelemetryMetricsResponse {
  metrics: TelemetryMetricResponse[];
  total: number;
  skip: number;
  limit: number;
}

export interface ListTelemetryLogsResponse {
  logs: TelemetryLogResponse[];
  total: number;
  skip: number;
  limit: number;
}

// Upload response types
export interface UploadTelemetryTracesResponse {
  traces: Array<{
    trace_id: string;
    span_count: number;
    tag_ids: string[];
    metadata?: Record<string, string>;
  }>;
}

export interface UploadTelemetryMetricsResponse {
  metrics: Array<{
    metric_id: string;
    name: string;
    type: string;
    data_point_count: number;
    tag_ids: string[];
    metadata?: Record<string, string>;
  }>;
}

export interface UploadTelemetryLogsResponse {
  logs: Array<{
    log_id: string;
    timestamp: string;
    severity?: string;
    body: string;
    tag_ids: string[];
    metadata?: Record<string, string>;
  }>;
}

// Parameter types for API calls
export interface UploadTelemetryTracesParams {
  organizationId: string;
  traces: TelemetryTrace[];
}

export interface UploadTelemetryMetricsParams {
  organizationId: string;
  metrics: TelemetryMetric[];
}

export interface UploadTelemetryLogsParams {
  organizationId: string;
  logs: TelemetryLog[];
}

export interface ListTelemetryTracesParams {
  organizationId: string;
  skip?: number;
  limit?: number;
  tagIds?: string;
  nameSearch?: string;
}

export interface ListTelemetryMetricsParams {
  organizationId: string;
  skip?: number;
  limit?: number;
  tagIds?: string;
  nameSearch?: string;
}

export interface ListTelemetryLogsParams {
  organizationId: string;
  skip?: number;
  limit?: number;
  tagIds?: string;
  severity?: string;
}

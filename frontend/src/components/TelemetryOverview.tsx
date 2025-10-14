'use client'

import { useState, useEffect } from 'react';
import { 
  listTelemetryTracesApi, 
  listTelemetryMetricsApi, 
  listTelemetryLogsApi 
} from '@/utils/api';
import { 
  ListTelemetryTracesResponse, 
  ListTelemetryMetricsResponse, 
  ListTelemetryLogsResponse 
} from '@/types/telemetry';

interface TelemetryOverviewProps {
  organizationId: string;
}

export default function TelemetryOverview({ organizationId }: TelemetryOverviewProps) {
  const [tracesData, setTracesData] = useState<ListTelemetryTracesResponse | null>(null);
  const [metricsData, setMetricsData] = useState<ListTelemetryMetricsResponse | null>(null);
  const [logsData, setLogsData] = useState<ListTelemetryLogsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchOverviewData = async () => {
      try {
        setLoading(true);
        const [traces, metrics, logs] = await Promise.all([
          listTelemetryTracesApi({ organizationId, limit: 5 }),
          listTelemetryMetricsApi({ organizationId, limit: 5 }),
          listTelemetryLogsApi({ organizationId, limit: 5 })
        ]);
        
        setTracesData(traces);
        setMetricsData(metrics);
        setLogsData(logs);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load telemetry data');
      } finally {
        setLoading(false);
      }
    };

    fetchOverviewData();
  }, [organizationId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-md p-4">
        <div className="flex">
          <div className="ml-3">
            <h3 className="text-sm font-medium text-red-800">Error loading telemetry data</h3>
            <div className="mt-2 text-sm text-red-700">{error}</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white overflow-hidden shadow rounded-lg">
          <div className="p-5">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <div className="w-8 h-8 bg-blue-500 rounded-md flex items-center justify-center">
                  <span className="text-white text-sm">🔍</span>
                </div>
              </div>
              <div className="ml-5 w-0 flex-1">
                <dl>
                  <dt className="text-sm font-medium text-gray-500 truncate">Total Traces</dt>
                  <dd className="text-lg font-medium text-gray-900">{tracesData?.total || 0}</dd>
                </dl>
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white overflow-hidden shadow rounded-lg">
          <div className="p-5">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <div className="w-8 h-8 bg-green-500 rounded-md flex items-center justify-center">
                  <span className="text-white text-sm">📈</span>
                </div>
              </div>
              <div className="ml-5 w-0 flex-1">
                <dl>
                  <dt className="text-sm font-medium text-gray-500 truncate">Total Metrics</dt>
                  <dd className="text-lg font-medium text-gray-900">{metricsData?.total || 0}</dd>
                </dl>
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white overflow-hidden shadow rounded-lg">
          <div className="p-5">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <div className="w-8 h-8 bg-yellow-500 rounded-md flex items-center justify-center">
                  <span className="text-white text-sm">📝</span>
                </div>
              </div>
              <div className="ml-5 w-0 flex-1">
                <dl>
                  <dt className="text-sm font-medium text-gray-500 truncate">Total Logs</dt>
                  <dd className="text-lg font-medium text-gray-900">{logsData?.total || 0}</dd>
                </dl>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Recent Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent Traces */}
        <div className="bg-white shadow rounded-lg">
          <div className="px-4 py-5 sm:p-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4">Recent Traces</h3>
            {tracesData?.traces.length === 0 ? (
              <p className="text-gray-500 text-sm">No traces found</p>
            ) : (
              <div className="space-y-3">
                {tracesData?.traces.map((trace) => (
                  <div key={trace.trace_id} className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {trace.trace_id}
                      </p>
                      <p className="text-sm text-gray-500">
                        {trace.span_count} spans • {new Date(trace.upload_date).toLocaleDateString()}
                      </p>
                    </div>
                    <div className="flex-shrink-0">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                        {trace.tag_ids.length} tags
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Recent Metrics */}
        <div className="bg-white shadow rounded-lg">
          <div className="px-4 py-5 sm:p-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4">Recent Metrics</h3>
            {metricsData?.metrics.length === 0 ? (
              <p className="text-gray-500 text-sm">No metrics found</p>
            ) : (
              <div className="space-y-3">
                {metricsData?.metrics.map((metric) => (
                  <div key={metric.metric_id} className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {metric.name}
                      </p>
                      <p className="text-sm text-gray-500">
                        {metric.type} • {metric.data_point_count} points
                      </p>
                    </div>
                    <div className="flex-shrink-0">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                        {metric.tag_ids.length} tags
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Recent Logs */}
        <div className="bg-white shadow rounded-lg">
          <div className="px-4 py-5 sm:p-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4">Recent Logs</h3>
            {logsData?.logs.length === 0 ? (
              <p className="text-gray-500 text-sm">No logs found</p>
            ) : (
              <div className="space-y-3">
                {logsData?.logs.map((log) => (
                  <div key={log.log_id} className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {log.body}
                      </p>
                      <p className="text-sm text-gray-500">
                        {log.severity} • {new Date(log.timestamp).toLocaleDateString()}
                      </p>
                    </div>
                    <div className="flex-shrink-0">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800">
                        {log.tag_ids.length} tags
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

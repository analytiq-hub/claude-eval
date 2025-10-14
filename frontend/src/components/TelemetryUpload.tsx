'use client'

import { useState } from 'react';
import { 
  uploadTelemetryTracesApi, 
  uploadTelemetryMetricsApi, 
  uploadTelemetryLogsApi 
} from '@/utils/api';

interface TelemetryUploadProps {
  organizationId: string;
}

export default function TelemetryUpload({ organizationId }: TelemetryUploadProps) {
  const [activeTab, setActiveTab] = useState<'traces' | 'metrics' | 'logs'>('traces');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Form states
  const [tracesData, setTracesData] = useState('');
  const [metricsData, setMetricsData] = useState('');
  const [logsData, setLogsData] = useState('');

  const handleUpload = async () => {
    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      let result;
      
      switch (activeTab) {
        case 'traces':
          const traces = JSON.parse(tracesData);
          result = await uploadTelemetryTracesApi({
            organizationId,
            traces: Array.isArray(traces) ? traces : [traces]
          });
          setSuccess(`Successfully uploaded ${result.traces.length} trace(s)`);
          setTracesData('');
          break;
          
        case 'metrics':
          const metrics = JSON.parse(metricsData);
          result = await uploadTelemetryMetricsApi({
            organizationId,
            metrics: Array.isArray(metrics) ? metrics : [metrics]
          });
          setSuccess(`Successfully uploaded ${result.metrics.length} metric(s)`);
          setMetricsData('');
          break;
          
        case 'logs':
          const logs = JSON.parse(logsData);
          result = await uploadTelemetryLogsApi({
            organizationId,
            logs: Array.isArray(logs) ? logs : [logs]
          });
          setSuccess(`Successfully uploaded ${result.logs.length} log(s)`);
          setLogsData('');
          break;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setLoading(false);
    }
  };

  const getSampleData = () => {
    switch (activeTab) {
      case 'traces':
        return JSON.stringify({
          resource_spans: [
            {
              resource: {
                attributes: [
                  { key: "service.name", value: { stringValue: "example-service" } },
                  { key: "service.version", value: { stringValue: "1.0.0" } }
                ]
              },
              scope_spans: [
                {
                  scope: {
                    name: "example-instrumentation",
                    version: "1.0.0"
                  },
                  spans: [
                    {
                      traceId: "12345678901234567890123456789012",
                      spanId: "1234567890123456",
                      name: "example-span",
                      kind: "SPAN_KIND_SERVER",
                      startTimeUnixNano: "1640995200000000000",
                      endTimeUnixNano: "1640995201000000000",
                      attributes: [
                        { key: "http.method", value: { stringValue: "GET" } },
                        { key: "http.url", value: { stringValue: "/example" } }
                      ],
                      status: { code: "STATUS_CODE_OK" }
                    }
                  ]
                }
              ]
            }
          ],
          tag_ids: [],
          metadata: { source: "manual-upload" }
        }, null, 2);
        
      case 'metrics':
        return JSON.stringify({
          name: "example_counter",
          description: "An example counter metric",
          unit: "1",
          type: "counter",
          data_points: [
            {
              timeUnixNano: "1640995200000000000",
              value: { asInt: "1" }
            }
          ],
          resource: {
            attributes: [
              { key: "service.name", value: { stringValue: "example-service" } }
            ]
          },
          tag_ids: [],
          metadata: { source: "manual-upload" }
        }, null, 2);
        
      case 'logs':
        return JSON.stringify({
          timestamp: new Date().toISOString(),
          severity: "INFO",
          body: "Example log message",
          attributes: {
            "logger.name": "example-logger",
            "thread.id": 123,
            "level": "INFO"
          },
          resource: {
            "service.name": "example-service",
            "service.version": "1.0.0"
          },
          trace_id: "12345678901234567890123456789012",
          span_id: "1234567890123456",
          tag_ids: [],
          metadata: { source: "manual-upload" }
        }, null, 2);
        
      default:
        return '';
    }
  };

  const loadSampleData = () => {
    const sample = getSampleData();
    switch (activeTab) {
      case 'traces':
        setTracesData(sample);
        break;
      case 'metrics':
        setMetricsData(sample);
        break;
      case 'logs':
        setLogsData(sample);
        break;
    }
  };

  const getCurrentData = () => {
    switch (activeTab) {
      case 'traces':
        return tracesData;
      case 'metrics':
        return metricsData;
      case 'logs':
        return logsData;
      default:
        return '';
    }
  };

  const setCurrentData = (data: string) => {
    switch (activeTab) {
      case 'traces':
        setTracesData(data);
        break;
      case 'metrics':
        setMetricsData(data);
        break;
      case 'logs':
        setLogsData(data);
        break;
    }
  };

  const tabs = [
    { id: 'traces', label: 'Traces', icon: '🔍' },
    { id: 'metrics', label: 'Metrics', icon: '📈' },
    { id: 'logs', label: 'Logs', icon: '📝' },
  ];

  return (
    <div className="space-y-6">
      {/* Tab Navigation */}
      <div className="border-b border-gray-200">
        <div className="flex gap-8">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as 'traces' | 'metrics' | 'logs')}
              className={`pb-4 px-1 relative font-semibold text-base flex items-center gap-2 ${
                activeTab === tab.id
                  ? 'text-blue-600 after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <span>{tab.icon}</span>
              <span>{tab.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Upload Form */}
      <div className="bg-white shadow rounded-lg">
        <div className="px-4 py-5 sm:p-6">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg leading-6 font-medium text-gray-900">
                Upload {activeTab.charAt(0).toUpperCase() + activeTab.slice(1)}
              </h3>
              <button
                onClick={loadSampleData}
                className="text-sm text-blue-600 hover:text-blue-500"
              >
                Load Sample Data
              </button>
            </div>

            <div>
              <label htmlFor="data" className="block text-sm font-medium text-gray-700 mb-2">
                JSON Data
              </label>
              <textarea
                id="data"
                rows={15}
                value={getCurrentData()}
                onChange={(e) => setCurrentData(e.target.value)}
                placeholder={`Enter ${activeTab} data in JSON format...`}
                className="block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm font-mono"
              />
            </div>

            <div className="flex items-center justify-between">
              <div className="text-sm text-gray-500">
                <p>Upload {activeTab} data in OpenTelemetry format.</p>
                <p>Use &quot;Load Sample Data&quot; to see an example.</p>
              </div>
              <button
                onClick={handleUpload}
                disabled={loading || !getCurrentData().trim()}
                className="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? 'Uploading...' : 'Upload'}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Success Message */}
      {success && (
        <div className="bg-green-50 border border-green-200 rounded-md p-4">
          <div className="flex">
            <div className="ml-3">
              <h3 className="text-sm font-medium text-green-800">Upload Successful</h3>
              <div className="mt-2 text-sm text-green-700">{success}</div>
            </div>
          </div>
        </div>
      )}

      {/* Error Message */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-4">
          <div className="flex">
            <div className="ml-3">
              <h3 className="text-sm font-medium text-red-800">Upload Failed</h3>
              <div className="mt-2 text-sm text-red-700">{error}</div>
            </div>
          </div>
        </div>
      )}

      {/* Help Section */}
      <div className="bg-gray-50 rounded-lg p-6">
        <h4 className="text-lg font-medium text-gray-900 mb-4">Upload Guidelines</h4>
        <div className="space-y-3 text-sm text-gray-600">
          <div>
            <strong>Traces:</strong> Upload OpenTelemetry trace data with resource_spans, scope_spans, and spans.
          </div>
          <div>
            <strong>Metrics:</strong> Upload metric data with name, type, data_points, and resource information.
          </div>
          <div>
            <strong>Logs:</strong> Upload log data with timestamp, severity, body, and attributes.
          </div>
          <div>
            <strong>Tags:</strong> All telemetry types support optional tag_ids for categorization.
          </div>
          <div>
            <strong>Metadata:</strong> Add custom metadata as key-value pairs for additional context.
          </div>
        </div>
      </div>
    </div>
  );
}

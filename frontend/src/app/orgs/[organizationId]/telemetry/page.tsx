'use client'

import { useSearchParams, useRouter } from 'next/navigation';
import TelemetryOverview from '@/components/TelemetryOverview';
import TelemetryTracesList from '@/components/TelemetryTracesList';
import TelemetryMetricsList from '@/components/TelemetryMetricsList';
import TelemetryLogsList from '@/components/TelemetryLogsList';
import TelemetryUpload from '@/components/TelemetryUpload';

export default function TelemetryPage({ params }: { params: { organizationId: string } }) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const tab = searchParams.get('tab') || 'overview';

  const handleTabChange = (newValue: string) => {
    router.push(`/orgs/${params.organizationId}/telemetry?tab=${newValue}`);
  };

  const tabs = [
    { id: 'overview', label: 'Overview', icon: '📊' },
    { id: 'traces', label: 'Traces', icon: '🔍' },
    { id: 'metrics', label: 'Metrics', icon: '📈' },
    { id: 'logs', label: 'Logs', icon: '📝' },
    { id: 'upload', label: 'Upload', icon: '⬆️' },
  ];

  return (
    <div className="p-4">
      <div className="border-b border-gray-200 mb-6">
        <div className="flex gap-8">
          {tabs.map((tabItem) => (
            <button
              key={tabItem.id}
              onClick={() => handleTabChange(tabItem.id)}
              className={`pb-4 px-1 relative font-semibold text-base flex items-center gap-2 ${
                tab === tabItem.id
                  ? 'text-blue-600 after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <span>{tabItem.icon}</span>
              <span>{tabItem.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="max-w-6xl mx-auto">
        <div role="tabpanel" hidden={tab !== 'overview'}>
          {tab === 'overview' && <TelemetryOverview organizationId={params.organizationId} />}
        </div>
        <div role="tabpanel" hidden={tab !== 'traces'}>
          {tab === 'traces' && <TelemetryTracesList organizationId={params.organizationId} />}
        </div>
        <div role="tabpanel" hidden={tab !== 'metrics'}>
          {tab === 'metrics' && <TelemetryMetricsList organizationId={params.organizationId} />}
        </div>
        <div role="tabpanel" hidden={tab !== 'logs'}>
          {tab === 'logs' && <TelemetryLogsList organizationId={params.organizationId} />}
        </div>
        <div role="tabpanel" hidden={tab !== 'upload'}>
          {tab === 'upload' && <TelemetryUpload organizationId={params.organizationId} />}
        </div>
      </div>
    </div>
  );
}

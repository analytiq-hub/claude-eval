'use client'

import { useState, useEffect, useCallback } from 'react';
import { listTelemetryTracesApi } from '@/utils/api';
import { TelemetryTraceResponse } from '@/types/telemetry';

interface TelemetryTracesListProps {
  organizationId: string;
}

export default function TelemetryTracesList({ organizationId }: TelemetryTracesListProps) {
  const [traces, setTraces] = useState<TelemetryTraceResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [limit] = useState(20);
  const [tagFilter, setTagFilter] = useState('');
  const [nameSearch, setNameSearch] = useState('');

  const fetchTraces = useCallback(async () => {
    try {
      setLoading(true);
      const response = await listTelemetryTracesApi({
        organizationId,
        skip,
        limit,
        tagIds: tagFilter || undefined,
        nameSearch: nameSearch || undefined,
      });
      
      setTraces(response.traces);
      setTotal(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load traces');
    } finally {
      setLoading(false);
    }
  }, [organizationId, skip, limit, tagFilter, nameSearch]);

  useEffect(() => {
    fetchTraces();
  }, [fetchTraces]);

  const handleSearch = () => {
    setSkip(0);
    fetchTraces();
  };

  const handlePreviousPage = () => {
    if (skip > 0) {
      setSkip(Math.max(0, skip - limit));
    }
  };

  const handleNextPage = () => {
    if (skip + limit < total) {
      setSkip(skip + limit);
    }
  };

  if (loading && traces.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Search and Filters */}
      <div className="bg-white shadow rounded-lg p-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label htmlFor="nameSearch" className="block text-sm font-medium text-gray-700">
              Search
            </label>
            <input
              type="text"
              id="nameSearch"
              value={nameSearch}
              onChange={(e) => setNameSearch(e.target.value)}
              placeholder="Search traces..."
              className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            />
          </div>
          <div>
            <label htmlFor="tagFilter" className="block text-sm font-medium text-gray-700">
              Tag IDs (comma-separated)
            </label>
            <input
              type="text"
              id="tagFilter"
              value={tagFilter}
              onChange={(e) => setTagFilter(e.target.value)}
              placeholder="tag1,tag2,tag3"
              className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={handleSearch}
              className="w-full bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
            >
              Search
            </button>
          </div>
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-4">
          <div className="flex">
            <div className="ml-3">
              <h3 className="text-sm font-medium text-red-800">Error loading traces</h3>
              <div className="mt-2 text-sm text-red-700">{error}</div>
            </div>
          </div>
        </div>
      )}

      {/* Traces List */}
      <div className="bg-white shadow rounded-lg">
        <div className="px-4 py-5 sm:p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg leading-6 font-medium text-gray-900">
              Traces ({total})
            </h3>
          </div>

          {traces.length === 0 ? (
            <div className="text-center py-12">
              <div className="text-gray-500">
                <p className="text-lg font-medium">No traces found</p>
                <p className="text-sm">Try adjusting your search criteria or upload some telemetry data.</p>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {traces.map((trace) => (
                <div key={trace.trace_id} className="border border-gray-200 rounded-lg p-4 hover:bg-gray-50">
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center space-x-3">
                        <div className="flex-shrink-0">
                          <div className="w-8 h-8 bg-blue-100 rounded-full flex items-center justify-center">
                            <span className="text-blue-600 text-sm font-medium">T</span>
                          </div>
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">
                            {trace.trace_id}
                          </p>
                          <div className="flex items-center space-x-4 text-sm text-gray-500">
                            <span>{trace.span_count} spans</span>
                            <span>•</span>
                            <span>Uploaded {new Date(trace.upload_date).toLocaleString()}</span>
                            <span>•</span>
                            <span>By {trace.uploaded_by}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div className="flex-shrink-0 flex items-center space-x-2">
                      {trace.tag_ids.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {trace.tag_ids.slice(0, 3).map((tagId) => (
                            <span
                              key={tagId}
                              className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800"
                            >
                              {tagId}
                            </span>
                          ))}
                          {trace.tag_ids.length > 3 && (
                            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
                              +{trace.tag_ids.length - 3}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  {trace.metadata && Object.keys(trace.metadata).length > 0 && (
                    <div className="mt-3 pt-3 border-t border-gray-200">
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(trace.metadata).map(([key, value]) => (
                          <span
                            key={key}
                            className="inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-gray-100 text-gray-800"
                          >
                            {key}: {value}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {total > limit && (
            <div className="mt-6 flex items-center justify-between">
              <div className="text-sm text-gray-700">
                Showing {skip + 1} to {Math.min(skip + limit, total)} of {total} traces
              </div>
              <div className="flex space-x-2">
                <button
                  onClick={handlePreviousPage}
                  disabled={skip === 0}
                  className="px-3 py-2 text-sm font-medium text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Previous
                </button>
                <button
                  onClick={handleNextPage}
                  disabled={skip + limit >= total}
                  className="px-3 py-2 text-sm font-medium text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

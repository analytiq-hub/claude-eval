import pytest
import pytest_asyncio
import json
import os
import sys
import logging
from datetime import datetime, UTC
from bson import ObjectId
from unittest.mock import patch

# Import shared test utilities
from .conftest_utils import (
    client, TEST_ORG_ID, TEST_USER_ID,
    get_auth_headers, get_token_headers
)
import analytiq_data as ad

logger = logging.getLogger(__name__)

# Check that ENV is set to pytest
assert os.environ["ENV"] == "pytest"

@pytest.fixture
def sample_trace_data():
    """Create sample OpenTelemetry trace data"""
    return {
        "resource_spans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "test-service"}},
                        {"key": "service.version", "value": {"stringValue": "1.0.0"}}
                    ]
                },
                "scope_spans": [
                    {
                        "scope": {
                            "name": "test-instrumentation",
                            "version": "1.0.0"
                        },
                        "spans": [
                            {
                                "traceId": "12345678901234567890123456789012",
                                "spanId": "1234567890123456",
                                "name": "test-span",
                                "kind": "SPAN_KIND_SERVER",
                                "startTimeUnixNano": "1640995200000000000",
                                "endTimeUnixNano": "1640995201000000000",
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "GET"}},
                                    {"key": "http.url", "value": {"stringValue": "/test"}}
                                ],
                                "status": {"code": "STATUS_CODE_OK"}
                            }
                        ]
                    }
                ]
            }
        ]
    }

@pytest.fixture
def sample_metric_data():
    """Create sample OpenTelemetry metric data"""
    return {
        "name": "test_counter",
        "description": "A test counter metric",
        "unit": "1",
        "type": "counter",
        "data_points": [
            {
                "timeUnixNano": "1640995200000000000",
                "asInt": "42"
            },
            {
                "timeUnixNano": "1640995201000000000",
                "asInt": "43"
            }
        ],
        "resource": {
            "attributes": [
                {"key": "service.name", "value": {"stringValue": "test-service"}}
            ]
        }
    }

@pytest.fixture
def sample_log_data():
    """Create sample OpenTelemetry log data"""
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "severity": "INFO",
        "body": "Test log message",
        "attributes": {
            "logger.name": "test-logger",
            "thread.id": 123,
            "level": "INFO"
        },
        "resource": {
            "service.name": "test-service",
            "service.version": "1.0.0"
        },
        "trace_id": "12345678901234567890123456789012",
        "span_id": "1234567890123456"
    }

@pytest.fixture
def sample_tag():
    """Create a sample tag for testing"""
    return {
        "name": "test-telemetry-tag",
        "description": "Tag for telemetry testing"
    }

@pytest_asyncio.fixture
async def created_tag(org_and_users, sample_tag):
    """Create a tag and return its ID"""
    org_id = org_and_users["org_id"]
    admin = org_and_users["admin"]
    headers = get_token_headers(admin["token"])
    response = client.post(
        f"/v0/orgs/{org_id}/tags",
        json=sample_tag,
        headers=headers
    )
    assert response.status_code == 200
    return response.json()["id"]

class TestTelemetryTraces:
    """Test OpenTelemetry traces endpoints"""

    @pytest.mark.asyncio
    async def test_upload_traces_success(self, org_and_users, sample_trace_data):
        """Test successful trace upload"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "traces": [
                {
                    **sample_trace_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "traces" in data
        assert len(data["traces"]) == 1
        assert "trace_id" in data["traces"][0]
        assert "span_count" in data["traces"][0]
        assert data["traces"][0]["span_count"] == 1

    @pytest.mark.asyncio
    async def test_upload_traces_with_tags(self, org_and_users, sample_trace_data, created_tag):
        """Test trace upload with tags"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "traces": [
                {
                    **sample_trace_data,
                    "tag_ids": [created_tag],
                    "metadata": {"source": "test-with-tags"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["traces"][0]["tag_ids"] == [created_tag]

    @pytest.mark.asyncio
    async def test_upload_traces_invalid_tag(self, org_and_users, sample_trace_data):
        """Test trace upload with invalid tag ID"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        invalid_tag_id = str(ObjectId())
        upload_data = {
            "traces": [
                {
                    **sample_trace_data,
                    "tag_ids": [invalid_tag_id],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 400
        assert "Invalid tag IDs" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_upload_traces_multiple(self, org_and_users, sample_trace_data):
        """Test uploading multiple traces"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "traces": [
                {
                    **sample_trace_data,
                    "tag_ids": [],
                    "metadata": {"source": "test1"}
                },
                {
                    **sample_trace_data,
                    "tag_ids": [],
                    "metadata": {"source": "test2"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["traces"]) == 2

    @pytest.mark.asyncio
    async def test_list_traces_empty(self, org_and_users):
        """Test listing traces when none exist"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/traces",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["traces"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_traces_with_data(self, org_and_users, sample_trace_data):
        """Test listing traces with data"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        
        # Upload a trace first
        upload_data = {
            "traces": [
                {
                    **sample_trace_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        upload_response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        assert upload_response.status_code == 200
        
        # List traces
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/traces",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["traces"]) == 1
        assert data["total"] == 1
        assert "trace_id" in data["traces"][0]
        assert "span_count" in data["traces"][0]
        assert "upload_date" in data["traces"][0]

    @pytest.mark.asyncio
    async def test_list_traces_with_pagination(self, org_and_users, sample_trace_data):
        """Test listing traces with pagination"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        
        # Upload multiple traces
        upload_data = {
            "traces": [
                {
                    **sample_trace_data,
                    "tag_ids": [],
                    "metadata": {"source": f"test{i}"}
                }
                for i in range(5)
            ]
        }
        upload_response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        assert upload_response.status_code == 200
        
        # Test pagination
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/traces?skip=0&limit=2",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["traces"]) == 2
        assert data["total"] == 5
        assert data["skip"] == 0
        assert data["limit"] == 2

    @pytest.mark.asyncio
    async def test_list_traces_with_tag_filter(self, org_and_users, sample_trace_data, created_tag):
        """Test listing traces with tag filter"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        
        # Upload traces with and without tags
        upload_data = {
            "traces": [
                {
                    **sample_trace_data,
                    "tag_ids": [created_tag],
                    "metadata": {"source": "tagged"}
                },
                {
                    **sample_trace_data,
                    "tag_ids": [],
                    "metadata": {"source": "untagged"}
                }
            ]
        }
        upload_response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        assert upload_response.status_code == 200
        
        # Filter by tag
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/traces?tag_ids={created_tag}",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["traces"]) == 1
        assert data["traces"][0]["tag_ids"] == [created_tag]

class TestTelemetryMetrics:
    """Test OpenTelemetry metrics endpoints"""

    @pytest.mark.asyncio
    async def test_upload_metrics_success(self, org_and_users, sample_metric_data):
        """Test successful metric upload"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "metrics": [
                {
                    **sample_metric_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data
        assert len(data["metrics"]) == 1
        assert "metric_id" in data["metrics"][0]
        assert "name" in data["metrics"][0]
        assert data["metrics"][0]["name"] == "test_counter"
        assert data["metrics"][0]["data_point_count"] == 2

    @pytest.mark.asyncio
    async def test_upload_metrics_with_tags(self, org_and_users, sample_metric_data, created_tag):
        """Test metric upload with tags"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "metrics": [
                {
                    **sample_metric_data,
                    "tag_ids": [created_tag],
                    "metadata": {"source": "test-with-tags"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["metrics"][0]["tag_ids"] == [created_tag]

    @pytest.mark.asyncio
    async def test_upload_metrics_invalid_tag(self, org_and_users, sample_metric_data):
        """Test metric upload with invalid tag ID"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        invalid_tag_id = str(ObjectId())
        upload_data = {
            "metrics": [
                {
                    **sample_metric_data,
                    "tag_ids": [invalid_tag_id],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 400
        assert "Invalid tag IDs" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_metrics_empty(self, org_and_users):
        """Test listing metrics when none exist"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["metrics"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_metrics_with_data(self, org_and_users, sample_metric_data):
        """Test listing metrics with data"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        
        # Upload a metric first
        upload_data = {
            "metrics": [
                {
                    **sample_metric_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        upload_response = client.post(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            json=upload_data,
            headers=headers
        )
        assert upload_response.status_code == 200
        
        # List metrics
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["metrics"]) == 1
        assert data["total"] == 1
        assert "metric_id" in data["metrics"][0]
        assert "name" in data["metrics"][0]
        assert "upload_date" in data["metrics"][0]

    @pytest.mark.asyncio
    async def test_list_metrics_with_name_search(self, org_and_users, sample_metric_data):
        """Test listing metrics with name search"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        
        # Upload metrics with different names
        upload_data = {
            "metrics": [
                {
                    **sample_metric_data,
                    "name": "test_counter",
                    "tag_ids": [],
                    "metadata": {"source": "test1"}
                },
                {
                    **sample_metric_data,
                    "name": "other_metric",
                    "tag_ids": [],
                    "metadata": {"source": "test2"}
                }
            ]
        }
        upload_response = client.post(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            json=upload_data,
            headers=headers
        )
        assert upload_response.status_code == 200
        
        # Search by name
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/metrics?name_search=counter",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["metrics"]) == 1
        assert data["metrics"][0]["name"] == "test_counter"

class TestTelemetryLogs:
    """Test OpenTelemetry logs endpoints"""

    @pytest.mark.asyncio
    async def test_upload_logs_success(self, org_and_users, sample_log_data):
        """Test successful log upload"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "logs": [
                {
                    **sample_log_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/logs",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert len(data["logs"]) == 1
        assert "log_id" in data["logs"][0]
        assert "body" in data["logs"][0]
        assert data["logs"][0]["body"] == "Test log message"

    @pytest.mark.asyncio
    async def test_upload_logs_with_tags(self, org_and_users, sample_log_data, created_tag):
        """Test log upload with tags"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "logs": [
                {
                    **sample_log_data,
                    "tag_ids": [created_tag],
                    "metadata": {"source": "test-with-tags"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/logs",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["logs"][0]["tag_ids"] == [created_tag]

    @pytest.mark.asyncio
    async def test_upload_logs_invalid_tag(self, org_and_users, sample_log_data):
        """Test log upload with invalid tag ID"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        invalid_tag_id = str(ObjectId())
        upload_data = {
            "logs": [
                {
                    **sample_log_data,
                    "tag_ids": [invalid_tag_id],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/logs",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 400
        assert "Invalid tag IDs" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_logs_empty(self, org_and_users):
        """Test listing logs when none exist"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/logs",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["logs"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_logs_with_data(self, org_and_users, sample_log_data):
        """Test listing logs with data"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        
        # Upload a log first
        upload_data = {
            "logs": [
                {
                    **sample_log_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        upload_response = client.post(
            f"/v0/orgs/{org_id}/telemetry/logs",
            json=upload_data,
            headers=headers
        )
        assert upload_response.status_code == 200
        
        # List logs
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/logs",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["logs"]) == 1
        assert data["total"] == 1
        assert "log_id" in data["logs"][0]
        assert "body" in data["logs"][0]
        assert "upload_date" in data["logs"][0]

    @pytest.mark.asyncio
    async def test_list_logs_with_severity_filter(self, org_and_users, sample_log_data):
        """Test listing logs with severity filter"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        
        # Upload logs with different severities
        upload_data = {
            "logs": [
                {
                    **sample_log_data,
                    "severity": "INFO",
                    "tag_ids": [],
                    "metadata": {"source": "test1"}
                },
                {
                    **sample_log_data,
                    "severity": "ERROR",
                    "body": "Error message",
                    "tag_ids": [],
                    "metadata": {"source": "test2"}
                }
            ]
        }
        upload_response = client.post(
            f"/v0/orgs/{org_id}/telemetry/logs",
            json=upload_data,
            headers=headers
        )
        assert upload_response.status_code == 200
        
        # Filter by severity
        response = client.get(
            f"/v0/orgs/{org_id}/telemetry/logs?severity=ERROR",
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["severity"] == "ERROR"
        assert data["logs"][0]["body"] == "Error message"

class TestTelemetryAuthentication:
    """Test OpenTelemetry endpoints authentication and authorization"""

    def test_upload_traces_unauthorized(self, sample_trace_data):
        """Test trace upload without authentication"""
        upload_data = {
            "traces": [
                {
                    **sample_trace_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{TEST_ORG_ID}/telemetry/traces",
            json=upload_data
        )
        
        assert response.status_code == 403

    def test_upload_metrics_unauthorized(self, sample_metric_data):
        """Test metric upload without authentication"""
        upload_data = {
            "metrics": [
                {
                    **sample_metric_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{TEST_ORG_ID}/telemetry/metrics",
            json=upload_data
        )
        
        assert response.status_code == 403

    def test_upload_logs_unauthorized(self, sample_log_data):
        """Test log upload without authentication"""
        upload_data = {
            "logs": [
                {
                    **sample_log_data,
                    "tag_ids": [],
                    "metadata": {"source": "test"}
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{TEST_ORG_ID}/telemetry/logs",
            json=upload_data
        )
        
        assert response.status_code == 403

    def test_list_traces_unauthorized(self):
        """Test listing traces without authentication"""
        response = client.get(f"/v0/orgs/{TEST_ORG_ID}/telemetry/traces")
        assert response.status_code == 403

    def test_list_metrics_unauthorized(self):
        """Test listing metrics without authentication"""
        response = client.get(f"/v0/orgs/{TEST_ORG_ID}/telemetry/metrics")
        assert response.status_code == 403

    def test_list_logs_unauthorized(self):
        """Test listing logs without authentication"""
        response = client.get(f"/v0/orgs/{TEST_ORG_ID}/telemetry/logs")
        assert response.status_code == 403

class TestTelemetryValidation:
    """Test OpenTelemetry endpoints validation"""

    @pytest.mark.asyncio
    async def test_upload_traces_invalid_data(self, org_and_users):
        """Test trace upload with invalid data structure"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "traces": [
                {
                    "invalid_field": "invalid_value"
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_upload_metrics_invalid_data(self, org_and_users):
        """Test metric upload with invalid data structure"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "metrics": [
                {
                    "invalid_field": "invalid_value"
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_upload_logs_invalid_data(self, org_and_users):
        """Test log upload with invalid data structure"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {
            "logs": [
                {
                    "invalid_field": "invalid_value"
                }
            ]
        }
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/logs",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_upload_traces_empty_list(self, org_and_users):
        """Test trace upload with empty traces list"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {"traces": []}
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/traces",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["traces"] == []

    @pytest.mark.asyncio
    async def test_upload_metrics_empty_list(self, org_and_users):
        """Test metric upload with empty metrics list"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {"metrics": []}
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/metrics",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["metrics"] == []

    @pytest.mark.asyncio
    async def test_upload_logs_empty_list(self, org_and_users):
        """Test log upload with empty logs list"""
        org_id = org_and_users["org_id"]
        admin = org_and_users["admin"]
        headers = get_token_headers(admin["token"])
        upload_data = {"logs": []}
        
        response = client.post(
            f"/v0/orgs/{org_id}/telemetry/logs",
            json=upload_data,
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["logs"] == []
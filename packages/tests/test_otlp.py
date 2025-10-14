"""
Tests for OTLP gRPC server functionality
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from docrouter_app.otlp_server import OTLPServer, OTLPTraceService, OTLPMetricsService, OTLPLogsService

class TestOTLPServer:
    """Test OTLP server functionality"""
    
    def test_otlp_server_initialization(self):
        """Test OTLP server initializes correctly"""
        server = OTLPServer(port=4317)
        assert server.port == 4317
        assert server.server is None
        assert server.organization_services == {}
    
    def test_add_organization_services(self):
        """Test adding organization services"""
        server = OTLPServer()
        organization_id = "test-org-123"
        
        server.add_organization_services(organization_id)
        
        assert organization_id in server.organization_services
        assert "trace_service" in server.organization_services[organization_id]
        assert "metrics_service" in server.organization_services[organization_id]
        assert "logs_service" in server.organization_services[organization_id]
        
        # Verify services are correct types
        assert isinstance(server.organization_services[organization_id]["trace_service"], OTLPTraceService)
        assert isinstance(server.organization_services[organization_id]["metrics_service"], OTLPMetricsService)
        assert isinstance(server.organization_services[organization_id]["logs_service"], OTLPLogsService)
    
    def test_remove_organization_services(self):
        """Test removing organization services"""
        server = OTLPServer()
        organization_id = "test-org-123"
        
        # Add then remove
        server.add_organization_services(organization_id)
        assert organization_id in server.organization_services
        
        server.remove_organization_services(organization_id)
        assert organization_id not in server.organization_services
    
    def test_get_organization_from_metadata_with_header(self):
        """Test extracting organization ID from metadata header"""
        server = OTLPServer()
        
        # Mock context with metadata
        context = MagicMock()
        context.invocation_metadata.return_value = [
            ("organization-id", "test-org-123"),
            ("other-header", "value")
        ]
        
        organization_id = server.get_organization_from_metadata(context)
        assert organization_id == "test-org-123"
    
    def test_get_organization_from_metadata_with_authority(self):
        """Test extracting organization ID from authority (subdomain)"""
        server = OTLPServer()
        
        # Mock context with authority
        context = MagicMock()
        context.invocation_metadata.return_value = [
            (":authority", "org-12345.localhost:4317"),
            ("other-header", "value")
        ]
        
        organization_id = server.get_organization_from_metadata(context)
        assert organization_id == "org-12345"
    
    def test_get_organization_from_metadata_no_match(self):
        """Test when no organization ID can be extracted"""
        server = OTLPServer()
        
        # Mock context without organization info
        context = MagicMock()
        context.invocation_metadata.return_value = [
            ("other-header", "value")
        ]
        
        organization_id = server.get_organization_from_metadata(context)
        assert organization_id is None

class TestOTLPTraceService:
    """Test OTLP Trace Service"""
    
    def test_trace_service_initialization(self):
        """Test trace service initializes correctly"""
        organization_id = "test-org-123"
        service = OTLPTraceService(organization_id)
        assert service.organization_id == organization_id
    
    @pytest.mark.asyncio
    async def test_convert_resource_span_empty(self):
        """Test converting empty resource span"""
        service = OTLPTraceService("test-org")
        
        # Mock empty resource span
        resource_span = MagicMock()
        resource_span.resource = None
        resource_span.scope_spans = []
        
        result = service._convert_resource_span(resource_span)
        assert result == {}

class TestOTLPMetricsService:
    """Test OTLP Metrics Service"""
    
    def test_metrics_service_initialization(self):
        """Test metrics service initializes correctly"""
        organization_id = "test-org-123"
        service = OTLPMetricsService(organization_id)
        assert service.organization_id == organization_id
    
    def test_get_metric_type_gauge(self):
        """Test metric type detection for gauge"""
        service = OTLPMetricsService("test-org")
        
        # Mock gauge metric
        metric = MagicMock()
        metric.HasField.return_value = True
        metric.gauge = MagicMock()
        
        with patch.object(metric, 'HasField') as mock_has_field:
            mock_has_field.side_effect = lambda field: field == "gauge"
            result = service._get_metric_type(metric)
            assert result == "gauge"
    
    def test_get_metric_type_counter(self):
        """Test metric type detection for counter"""
        service = OTLPMetricsService("test-org")
        
        # Mock sum metric (counter)
        metric = MagicMock()
        metric.sum = MagicMock()
        metric.sum.is_monotonic = True
        
        with patch.object(metric, 'HasField') as mock_has_field:
            mock_has_field.side_effect = lambda field: field == "sum"
            result = service._get_metric_type(metric)
            assert result == "counter"

class TestOTLPLogsService:
    """Test OTLP Logs Service"""
    
    def test_logs_service_initialization(self):
        """Test logs service initializes correctly"""
        organization_id = "test-org-123"
        service = OTLPLogsService(organization_id)
        assert service.organization_id == organization_id
    
    def test_get_severity_name_mapping(self):
        """Test severity number to name mapping"""
        service = OTLPLogsService("test-org")
        
        # Test various severity levels
        assert service._get_severity_name(1) == "TRACE"
        assert service._get_severity_name(5) == "DEBUG"
        assert service._get_severity_name(9) == "INFO"
        assert service._get_severity_name(13) == "WARN"
        assert service._get_severity_name(17) == "ERROR"
        assert service._get_severity_name(21) == "FATAL"
        assert service._get_severity_name(999) == "INFO"  # Default fallback
    
    def test_convert_attributes(self):
        """Test attribute conversion"""
        service = OTLPLogsService("test-org")
        
        # Mock attributes
        attr1 = MagicMock()
        attr1.key = "string_attr"
        attr1.value.HasField.return_value = True
        attr1.value.string_value = "test_value"
        
        attr2 = MagicMock()
        attr2.key = "int_attr"
        attr2.value.HasField.side_effect = lambda field: field == "int_value"
        attr2.value.int_value = 42
        
        attributes = [attr1, attr2]
        
        with patch.object(attr1.value, 'HasField') as mock_has_field1:
            with patch.object(attr2.value, 'HasField') as mock_has_field2:
                mock_has_field1.return_value = True
                mock_has_field2.side_effect = lambda field: field == "int_value"
                
                result = service._convert_attributes(attributes)
                assert result == {"string_attr": "test_value", "int_attr": 42}

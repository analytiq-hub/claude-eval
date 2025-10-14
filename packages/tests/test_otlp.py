"""
Tests for OTLP gRPC server functionality
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from docrouter_app.otlp_server import (
    add_organization_services, remove_organization_services,
    get_organization_from_metadata, get_organization_from_token,
    export_traces, export_metrics, export_logs,
    convert_resource_span, get_metric_type, convert_data_points,
    get_severity_name, convert_attributes, convert_resource
)
from docrouter_app.auth import get_org_id_from_token

# Test OTLP server functionality
def test_add_organization_services():
    """Test adding organization services"""
    add_organization_services("test-org-123")
    
    from docrouter_app.otlp_server import _organization_services
    assert "test-org-123" in _organization_services
    assert _organization_services["test-org-123"]["organization_id"] == "test-org-123"

def test_remove_organization_services():
    """Test removing organization services"""
    add_organization_services("test-org-123")
    
    from docrouter_app.otlp_server import _organization_services
    assert "test-org-123" in _organization_services
    
    remove_organization_services("test-org-123")
    assert "test-org-123" not in _organization_services

def test_get_organization_from_metadata_with_header():
    """Test extracting organization ID from metadata header"""
    # Mock context with organization header
    context = MagicMock()
    context.invocation_metadata.return_value = [
        ("organization-id", "org-12345"),
        ("other-header", "value")
    ]
    
    organization_id = get_organization_from_metadata(context)
    assert organization_id == "org-12345"

def test_get_organization_from_metadata_with_authority():
    """Test extracting organization ID from authority (subdomain)"""
    # Mock context with authority
    context = MagicMock()
    context.invocation_metadata.return_value = [
        (":authority", "org-12345.localhost:4317"),
        ("other-header", "value")
    ]
    
    organization_id = get_organization_from_metadata(context)
    assert organization_id == "org-12345"

def test_get_organization_from_metadata_no_match():
    """Test when no organization ID can be extracted"""
    # Mock context without organization info
    context = MagicMock()
    context.invocation_metadata.return_value = [
        ("other-header", "value")
    ]
    
    organization_id = get_organization_from_metadata(context)
    assert organization_id is None

@pytest.mark.asyncio
async def test_get_organization_from_token_with_bearer():
    """Test extracting organization ID from Bearer token"""
    # Mock context with Bearer token
    context = MagicMock()
    context.invocation_metadata.return_value = [
        ("authorization", "Bearer test-token-123"),
        ("other-header", "value")
    ]
    
    # Mock the centralized auth function
    with patch('docrouter_app.auth.get_org_id_from_token') as mock_get_org_id:
        mock_get_org_id.return_value = "test-org-123"
        
        organization_id = await get_organization_from_token(context)
        assert organization_id == "test-org-123"
        
        # Verify the auth function was called with the correct token
        mock_get_org_id.assert_called_once_with("test-token-123")

@pytest.mark.asyncio
async def test_get_organization_from_token_no_bearer():
    """Test when no Bearer token is present"""
    # Mock context without Bearer token
    context = MagicMock()
    context.invocation_metadata.return_value = [
        ("other-header", "value")
    ]
    
    organization_id = await get_organization_from_token(context)
    assert organization_id is None

@pytest.mark.asyncio
async def test_get_organization_from_token_invalid_token():
    """Test when token is invalid or not found"""
    # Mock context with Bearer token
    context = MagicMock()
    context.invocation_metadata.return_value = [
        ("authorization", "Bearer invalid-token"),
    ]
    
    # Mock the centralized auth function
    with patch('docrouter_app.auth.get_org_id_from_token') as mock_get_org_id:
        mock_get_org_id.return_value = None
        
        organization_id = await get_organization_from_token(context)
        assert organization_id is None
        
        # Verify the auth function was called with the invalid token
        mock_get_org_id.assert_called_once_with("invalid-token")

# Test OTLP Trace Service Functions
def test_convert_resource_span_empty():
    """Test converting empty resource span"""
    # Mock empty resource span
    resource_span = MagicMock()
    resource_span.resource = None
    resource_span.scope_spans = []
    
    result = convert_resource_span(resource_span)
    assert result == {}

# Test OTLP Metrics Service Functions
def test_get_metric_type_gauge():
    """Test getting metric type for gauge"""
    # Mock gauge metric
    metric = MagicMock()
    metric.HasField.side_effect = lambda field: field == "gauge"
    
    metric_type = get_metric_type(metric)
    assert metric_type == "gauge"

def test_get_metric_type_counter():
    """Test getting metric type for counter"""
    # Mock counter metric
    metric = MagicMock()
    metric.HasField.side_effect = lambda field: field == "sum"
    metric.sum.is_monotonic = True
    
    metric_type = get_metric_type(metric)
    assert metric_type == "counter"

# Test OTLP Logs Service Functions
def test_get_severity_name_mapping():
    """Test severity number to name mapping"""
    assert get_severity_name(1) == "TRACE"
    assert get_severity_name(5) == "DEBUG"
    assert get_severity_name(9) == "INFO"
    assert get_severity_name(13) == "WARN"
    assert get_severity_name(17) == "ERROR"
    assert get_severity_name(21) == "FATAL"
    assert get_severity_name(99) == "INFO"  # Default fallback

def test_convert_attributes():
    """Test converting attributes"""
    # Mock attributes
    attr1 = MagicMock()
    attr1.key = "string_attr"
    attr1.value.HasField.side_effect = lambda field: field == "string_value"
    attr1.value.string_value = "test_value"
    
    attr2 = MagicMock()
    attr2.key = "int_attr"
    attr2.value.HasField.side_effect = lambda field: field == "int_value"
    attr2.value.int_value = 42
    
    attributes = [attr1, attr2]
    result = convert_attributes(attributes)
    
    assert result == {"string_attr": "test_value", "int_attr": 42}

# Test Auth Functions
@pytest.mark.asyncio
async def test_get_org_id_from_token_valid():
    """Test getting organization ID from valid token"""
    # Mock the database and crypto functions
    with patch('analytiq_data.crypto.encrypt_token') as mock_encrypt:
        with patch('analytiq_data.common.get_async_db') as mock_get_db:
            mock_encrypt.return_value = "encrypted-token"
            
            # Mock database response
            mock_db = AsyncMock()
            mock_db.access_tokens.find_one.return_value = {
                "organization_id": "test-org-123",
                "user_id": "user-123"
            }
            mock_get_db.return_value = mock_db
            
            organization_id = await get_org_id_from_token("test-token-123")
            assert organization_id == "test-org-123"
            
            # Verify the token was encrypted and queried
            mock_encrypt.assert_called_once_with("test-token-123")
            mock_db.access_tokens.find_one.assert_called_once_with({"token": "encrypted-token"})

@pytest.mark.asyncio
async def test_get_org_id_from_token_invalid():
    """Test getting organization ID from invalid token"""
    # Mock the database and crypto functions
    with patch('analytiq_data.crypto.encrypt_token') as mock_encrypt:
        with patch('analytiq_data.common.get_async_db') as mock_get_db:
            mock_encrypt.return_value = "encrypted-invalid-token"
            
            # Mock database response - token not found
            mock_db = AsyncMock()
            mock_db.access_tokens.find_one.return_value = None
            mock_get_db.return_value = mock_db
            
            organization_id = await get_org_id_from_token("invalid-token")
            assert organization_id is None

@pytest.mark.asyncio
async def test_get_org_id_from_token_no_org():
    """Test getting organization ID from token with no organization"""
    # Mock the database and crypto functions
    with patch('analytiq_data.crypto.encrypt_token') as mock_encrypt:
        with patch('analytiq_data.common.get_async_db') as mock_get_db:
            mock_encrypt.return_value = "encrypted-account-token"
            
            # Mock database response - account-level token (no organization)
            mock_db = AsyncMock()
            mock_db.access_tokens.find_one.return_value = {
                "organization_id": None,
                "user_id": "user-123"
            }
            mock_get_db.return_value = mock_db
            
            organization_id = await get_org_id_from_token("account-token")
            assert organization_id is None
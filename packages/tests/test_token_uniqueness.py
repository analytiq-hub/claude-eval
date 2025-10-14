"""
Tests for token uniqueness validation
"""

import pytest
from unittest.mock import patch, AsyncMock
from docrouter_app.main import create_org_token, create_account_token
from docrouter_app.models import CreateAccessTokenRequest, User
from fastapi import HTTPException

@pytest.mark.asyncio
async def test_org_token_uniqueness_success():
    """Test successful token creation when encrypted token is unique"""
    # Mock dependencies
    mock_user = User(user_id="user123", user_name="testuser", token_type="jwt")
    mock_request = CreateAccessTokenRequest(name="test-token", lifetime=3600)
    
    with patch('docrouter_app.main.is_system_admin') as mock_is_admin:
        with patch('docrouter_app.main.is_organization_member') as mock_is_member:
            with patch('analytiq_data.common.get_async_db') as mock_get_db:
                with patch('analytiq_data.crypto.encrypt_token') as mock_encrypt:
                    with patch('secrets.token_urlsafe') as mock_token_gen:
                        with patch('docrouter_app.main.datetime') as mock_datetime:
                            
                            # Setup mocks
                            mock_is_admin.return_value = False
                            mock_is_member.return_value = True
                            mock_token_gen.return_value = "unique-token-123"
                            mock_encrypt.return_value = "encrypted-unique-token"
                            mock_datetime.now.return_value.isoformat.return_value = "2024-01-01T00:00:00Z"
                            
                            # Mock database
                            mock_db = AsyncMock()
                            mock_db.access_tokens.find_one.return_value = None  # No existing token
                            mock_db.access_tokens.insert_one.return_value = AsyncMock(inserted_id="token_id_123")
                            mock_get_db.return_value = mock_db
                            
                            # Call function
                            result = await create_org_token("org123", mock_request, mock_user)
                            
                            # Verify
                            assert result["token"] == "unique-token-123"
                            assert result["organization_id"] == "org123"
                            mock_db.access_tokens.find_one.assert_called_once_with({"token": "encrypted-unique-token"})
                            mock_db.access_tokens.insert_one.assert_called_once()

@pytest.mark.asyncio
async def test_org_token_uniqueness_retry_success():
    """Test token creation with retry when first encrypted token exists"""
    # Mock dependencies
    mock_user = User(user_id="user123", user_name="testuser", token_type="jwt")
    mock_request = CreateAccessTokenRequest(name="test-token", lifetime=3600)
    
    with patch('docrouter_app.main.is_system_admin') as mock_is_admin:
        with patch('docrouter_app.main.is_organization_member') as mock_is_member:
            with patch('analytiq_data.common.get_async_db') as mock_get_db:
                with patch('analytiq_data.crypto.encrypt_token') as mock_encrypt:
                    with patch('secrets.token_urlsafe') as mock_token_gen:
                        with patch('docrouter_app.main.datetime') as mock_datetime:
                            
                            # Setup mocks
                            mock_is_admin.return_value = False
                            mock_is_member.return_value = True
                            
                            # First token exists, second is unique
                            mock_token_gen.side_effect = ["duplicate-token", "unique-token-456"]
                            mock_encrypt.side_effect = ["encrypted-duplicate", "encrypted-unique"]
                            mock_datetime.now.return_value.isoformat.return_value = "2024-01-01T00:00:00Z"
                            
                            # Mock database
                            mock_db = AsyncMock()
                            # First call returns existing token, second call returns None (unique)
                            mock_db.access_tokens.find_one.side_effect = [{"_id": "existing"}, None]
                            mock_db.access_tokens.insert_one.return_value = AsyncMock(inserted_id="token_id_456")
                            mock_get_db.return_value = mock_db
                            
                            # Call function
                            result = await create_org_token("org123", mock_request, mock_user)
                            
                            # Verify
                            assert result["token"] == "unique-token-456"
                            assert result["organization_id"] == "org123"
                            assert mock_db.access_tokens.find_one.call_count == 2
                            mock_db.access_tokens.insert_one.assert_called_once()

@pytest.mark.asyncio
async def test_org_token_uniqueness_max_retries_exceeded():
    """Test error when max retries exceeded for unique token generation"""
    # Mock dependencies
    mock_user = User(user_id="user123", user_name="testuser", token_type="jwt")
    mock_request = CreateAccessTokenRequest(name="test-token", lifetime=3600)
    
    with patch('docrouter_app.main.is_system_admin') as mock_is_admin:
        with patch('docrouter_app.main.is_organization_member') as mock_is_member:
            with patch('analytiq_data.common.get_async_db') as mock_get_db:
                with patch('analytiq_data.crypto.encrypt_token') as mock_encrypt:
                    with patch('secrets.token_urlsafe') as mock_token_gen:
                        
                        # Setup mocks
                        mock_is_admin.return_value = False
                        mock_is_member.return_value = True
                        mock_token_gen.return_value = "duplicate-token"
                        mock_encrypt.return_value = "encrypted-duplicate"
                        
                        # Mock database - always returns existing token
                        mock_db = AsyncMock()
                        mock_db.access_tokens.find_one.return_value = {"_id": "existing"}
                        mock_get_db.return_value = mock_db
                        
                        # Call function and expect exception
                        with pytest.raises(HTTPException) as exc_info:
                            await create_org_token("org123", mock_request, mock_user)
                        
                        assert exc_info.value.status_code == 500
                        assert "Unable to generate unique token after multiple attempts" in str(exc_info.value.detail)
                        assert mock_db.access_tokens.find_one.call_count == 10  # Max retries

@pytest.mark.asyncio
async def test_account_token_uniqueness_success():
    """Test successful account token creation when encrypted token is unique"""
    # Mock dependencies
    mock_user = User(user_id="user123", user_name="testuser", token_type="jwt")
    mock_request = CreateAccessTokenRequest(name="account-token", lifetime=3600)
    
    with patch('analytiq_data.common.get_async_db') as mock_get_db:
        with patch('analytiq_data.crypto.encrypt_token') as mock_encrypt:
            with patch('secrets.token_urlsafe') as mock_token_gen:
                with patch('docrouter_app.main.datetime') as mock_datetime:
                    
                    # Setup mocks
                    mock_token_gen.return_value = "unique-account-token"
                    mock_encrypt.return_value = "encrypted-unique-account"
                    mock_datetime.now.return_value.isoformat.return_value = "2024-01-01T00:00:00Z"
                    
                    # Mock database
                    mock_db = AsyncMock()
                    mock_db.access_tokens.find_one.return_value = None  # No existing token
                    mock_db.access_tokens.insert_one.return_value = AsyncMock(inserted_id="account_token_id")
                    mock_get_db.return_value = mock_db
                    
                    # Call function
                    result = await create_account_token(mock_request, mock_user)
                    
                    # Verify
                    assert result["token"] == "unique-account-token"
                    assert result["organization_id"] is None
                    mock_db.access_tokens.find_one.assert_called_once_with({"token": "encrypted-unique-account"})
                    mock_db.access_tokens.insert_one.assert_called_once()

@pytest.mark.asyncio
async def test_cross_type_token_uniqueness():
    """Test that org tokens and account tokens share the same uniqueness constraint"""
    # This test verifies that an org token and account token can't have the same encrypted value
    # even though they're different types of tokens
    
    # Mock dependencies
    mock_user = User(user_id="user123", user_name="testuser", token_type="jwt")
    mock_request = CreateAccessTokenRequest(name="test-token", lifetime=3600)
    
    with patch('docrouter_app.main.is_system_admin') as mock_is_admin:
        with patch('docrouter_app.main.is_organization_member') as mock_is_member:
            with patch('analytiq_data.common.get_async_db') as mock_get_db:
                with patch('analytiq_data.crypto.encrypt_token') as mock_encrypt:
                    with patch('secrets.token_urlsafe') as mock_token_gen:
                        with patch('docrouter_app.main.datetime') as mock_datetime:
                            
                            # Setup mocks
                            mock_is_admin.return_value = False
                            mock_is_member.return_value = True
                            mock_token_gen.return_value = "same-token"
                            mock_encrypt.return_value = "same-encrypted-token"
                            mock_datetime.now.return_value.isoformat.return_value = "2024-01-01T00:00:00Z"
                            
                            # Mock database - simulate existing account token with same encrypted value
                            mock_db = AsyncMock()
                            mock_db.access_tokens.find_one.return_value = {
                                "_id": "existing_account_token",
                                "organization_id": None  # This is an account token
                            }
                            mock_get_db.return_value = mock_db
                            
                            # Call function and expect retry logic to kick in
                            # We'll mock the second attempt to succeed
                            mock_token_gen.side_effect = ["same-token", "different-token"]
                            mock_encrypt.side_effect = ["same-encrypted-token", "different-encrypted-token"]
                            mock_db.access_tokens.find_one.side_effect = [
                                {"_id": "existing_account_token", "organization_id": None},  # First call finds existing
                                None  # Second call finds no existing token
                            ]
                            mock_db.access_tokens.insert_one.return_value = AsyncMock(inserted_id="new_org_token")
                            
                            result = await create_org_token("org123", mock_request, mock_user)
                            
                            # Verify that it retried and succeeded
                            assert result["token"] == "different-token"
                            assert mock_db.access_tokens.find_one.call_count == 2

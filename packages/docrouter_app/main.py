# main.py

# Standard library imports
from datetime import datetime, timedelta, UTC
import os
import sys
import json
import secrets
import base64
import re
import uuid
import logging
import hmac
import hashlib
import asyncio
import warnings
from typing import Optional, List
from contextlib import asynccontextmanager
# Defer litellm import to avoid event loop warnings
# import litellm

# Suppress Pydantic deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="pydantic")

# Set up the path first, before other imports
cwd = os.path.dirname(os.path.abspath(__file__))
sys.path.append(f"{cwd}/..")

# Third-party imports
from fastapi import (
    FastAPI, File, UploadFile, HTTPException, Query, 
    Depends, status, Body, Security, Response, 
    BackgroundTasks, Request
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from jose import JWTError, jwt
from bcrypt import hashpw, gensalt
from dotenv import load_dotenv
from jsonschema import validate, ValidationError, Draft7Validator
from fastapi.encoders import jsonable_encoder
import httpx
from urllib.parse import urlparse

# Local imports
from docrouter_app import email_utils, startup, organizations, users, limits
from docrouter_app.auth import (
    get_current_user,
    get_admin_user,
    get_session_user,
    get_org_user,
    is_system_admin,
    is_organization_admin,
    is_organization_member
)
from docrouter_app.models import (
    User, AccessToken, ListAccessTokensResponse,
    CreateAccessTokenRequest,
    ListDocumentsResponse,
    DocumentMetadata, DocumentUpload, DocumentsUpload,
    DocumentUpdate,
    LLMModel, ListLLMModelsResponse,
    LLMProvider, ListLLMProvidersResponse, SetLLMProviderConfigRequest,
    AWSConfig,
    GetOCRMetadataResponse,
    LLMRunResponse,
    LLMResult,
    UpdateLLMResultRequest,
    Schema, SchemaConfig, ListSchemasResponse,
    Prompt, PromptConfig, ListPromptsResponse,
    Form, FormConfig, ListFormsResponse,
    FormSubmissionData, FormSubmission,
    TagConfig, Tag, ListTagsResponse,
    DocumentResponse,
    UserCreate, UserUpdate, UserResponse, ListUsersResponse,
    OrganizationMember,
    OrganizationCreate,
    OrganizationUpdate,
    Organization,
    ListOrganizationsResponse,
    InvitationResponse,
    CreateInvitationRequest,
    ListInvitationsResponse,
    AcceptInvitationRequest,
    FlowConfig,
    Flow,
    ListFlowsResponse,
    LLMPromptRequest,
    OAuthSignInRequest,
    OAuthSignInResponse,
    # OpenTelemetry models
    TelemetryTrace,
    TelemetryMetric,
    TelemetryLog,
    TelemetryTracesUpload,
    TelemetryMetricsUpload,
    TelemetryLogsUpload,
    TelemetryTraceResponse,
    TelemetryMetricResponse,
    TelemetryLogResponse,
    ListTelemetryTracesResponse,
    ListTelemetryMetricsResponse,
    ListTelemetryLogsResponse,
)
from docrouter_app.payments import payments_router, SPUCreditException
from docrouter_app.payments import (
    init_payments,
    sync_customer,
    delete_payments_customer,
)
import analytiq_data as ad
from analytiq_data.common.doc import get_mime_type

# OTLP imports
import grpc
from concurrent import futures
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2_grpc
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2_grpc

# Import OTLP server
from .otlp_server import start_otlp_server, stop_otlp_server, add_organization_to_otlp, remove_organization_from_otlp

# Set up the environment variables. This reads the .env file.
ad.common.setup()

# Environment variables
ENV = os.getenv("ENV", "dev")
NEXTAUTH_URL = os.getenv("NEXTAUTH_URL")
FASTAPI_ROOT_PATH = os.getenv("FASTAPI_ROOT_PATH", "/")
MONGODB_URI = os.getenv("MONGODB_URI")
SES_FROM_EMAIL = os.getenv("SES_FROM_EMAIL")

logger = logging.getLogger(__name__)

logger.info(f"ENV: {ENV}")
logger.info(f"NEXTAUTH_URL: {NEXTAUTH_URL}")
logger.info(f"FASTAPI_ROOT_PATH: {FASTAPI_ROOT_PATH}")
logger.info(f"MONGODB_URI: {MONGODB_URI}")
logger.info(f"SES_FROM_EMAIL: {SES_FROM_EMAIL}")
# JWT settings
NEXTAUTH_SECRET = os.getenv("NEXTAUTH_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Define MongoDB connection check function to be used in lifespan
async def check_mongodb_connection(uri):
    try:
        # Create a MongoDB client
        mongo_client = AsyncIOMotorClient(uri)
        
        # The ismaster command is cheap and does not require auth
        await mongo_client.admin.command('ismaster')
        logger.info(f"MongoDB connection successful at {uri}")
        return True
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB at {uri}: {e}")
        return False

UPLOAD_DIR = "data"

def decode_base64_content(content: str) -> bytes:
    """
    Decode base64 content that may be either:
    1. A data URL: 'data:application/pdf;base64,JVBERi0xLjQK...'
    2. Plain base64: 'JVBERi0xLjQK...'
    """
    try:
        # Check if it's a data URL
        if content.startswith('data:'):
            # Extract the base64 part after the comma
            base64_part = content.split(',', 1)[1]
            return base64.b64decode(base64_part)
        else:
            # Assume it's plain base64
            return base64.b64decode(content)
    except (IndexError, ValueError) as e:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid base64 content: {str(e)}"
        )

@asynccontextmanager
async def lifespan(app):
    # Startup code (previously in @app.on_event("startup"))
    
    # Check MongoDB connectivity first
    await check_mongodb_connection(MONGODB_URI)
    
    analytiq_client = ad.common.get_analytiq_client()
    await startup.setup_admin(analytiq_client)
    await startup.setup_api_creds(analytiq_client)
    
    # Initialize payments
    db = ad.common.get_async_db(analytiq_client)
    await init_payments(db)
    
    # Start OTLP gRPC server
    try:
        logger.info("Starting OTLP gRPC server...")
        await start_otlp_server()
        logger.info("OTLP gRPC server started successfully")
    except Exception as e:
        logger.error(f"OTLP gRPC server startup failed: {e}")
        # Don't raise here as OTLP might not be critical for startup
    
    yield  # This is where the app runs
    
    # Shutdown code (if any) would go here
    try:
        logger.info("Stopping OTLP gRPC server...")
        await stop_otlp_server()
        logger.info("OTLP gRPC server stopped successfully")
    except Exception as e:
        logger.error(f"Error stopping OTLP gRPC server: {e}")
    
    # For example: await some_client.close()

# Create the FastAPI app with the lifespan
app = FastAPI(
    root_path=FASTAPI_ROOT_PATH,
    lifespan=lifespan
)
security = HTTPBearer()

# CORS allowed origins
CORS_ORIGINS_DEF = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://host.docker.internal:3000",
]

# Only add NEXTAUTH_URL if it's not None
if NEXTAUTH_URL:
    CORS_ORIGINS_DEF.append(NEXTAUTH_URL)

cors_origins = os.getenv("CORS_ORIGINS", ",".join(CORS_ORIGINS_DEF)).split(",")
logger.info(f"CORS allowed origins: {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"] # Needed to expose the Content-Disposition header to the frontend
)

# Organization-level access tokens
@app.post("/v0/orgs/{organization_id}/access_tokens", response_model=AccessToken, tags=["access_tokens"])
async def create_org_token(
    organization_id: str,
    request: CreateAccessTokenRequest,
    current_user: User = Depends(get_current_user)
):
    """Create an organization-level API token"""
    # Verify organization membership
    is_sys_admin = await is_system_admin(current_user.user_id)
    is_org_member = await is_organization_member(organization_id, current_user.user_id)

    if not is_sys_admin and not is_org_member:
        raise HTTPException(status_code=403, detail="You are not authorized to create an organization-level API token")
    
    db = ad.common.get_async_db()
    
    # Generate unique token - retry if encrypted token already exists
    max_retries = 10
    for attempt in range(max_retries):
        token = secrets.token_urlsafe(32)
        encrypted_token = ad.crypto.encrypt_token(token)
        
        # Check if this encrypted token already exists
        existing_token = await db.access_tokens.find_one({"token": encrypted_token})
        if not existing_token:
            break
        
        if attempt == max_retries - 1:
            raise HTTPException(
                status_code=500, 
                detail="Unable to generate unique token after multiple attempts"
            )
    
    new_token = {
        "user_id": current_user.user_id,
        "organization_id": organization_id,
        "name": request.name,
        "token": encrypted_token,
        "created_at": datetime.now(UTC),
        "lifetime": request.lifetime
    }
    result = await db.access_tokens.insert_one(new_token)

    new_token["token"] = token  # Return plaintext token to user
    new_token["id"] = str(result.inserted_id)
    return new_token

@app.get("/v0/orgs/{organization_id}/access_tokens", response_model=ListAccessTokensResponse, tags=["access_tokens"])
async def list_org_tokens(
    organization_id: str,
    current_user: User = Depends(get_current_user)
):
    """List organization-level API tokens"""
    # Verify organization membership
    is_sys_admin = await is_system_admin(current_user.user_id)
    is_org_member = await is_organization_member(organization_id, current_user.user_id)

    if not is_sys_admin and not is_org_member:
        raise HTTPException(status_code=403, detail="You are not authorized to list organization-level API tokens")
    
    db = ad.common.get_async_db()
    cursor = db.access_tokens.find({
        "user_id": current_user.user_id,
        "organization_id": organization_id
    })
    tokens = await cursor.to_list(length=None)
    ret = [
        {
            "id": str(token["_id"]),
            "user_id": token["user_id"],
            "organization_id": token["organization_id"],
            "name": token["name"],
            "token": token["token"],
            "created_at": token["created_at"],
            "lifetime": token["lifetime"]
        }
        for token in tokens
    ]
    return ListAccessTokensResponse(access_tokens=ret)

@app.delete("/v0/orgs/{organization_id}/access_tokens/{token_id}", tags=["access_tokens"])
async def delete_org_token(
    organization_id: str,
    token_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete an organization-level API token"""
    # Verify organization membership
    is_sys_admin = await is_system_admin(current_user.user_id)
    is_org_member = await is_organization_member(organization_id, current_user.user_id)

    if not is_sys_admin and not is_org_member:
        raise HTTPException(status_code=403, detail="You are not authorized to delete an organization-level API token")
    
    db = ad.common.get_async_db()
    result = await db.access_tokens.delete_one({
        "_id": ObjectId(token_id),
        "user_id": current_user.user_id,
        "organization_id": organization_id
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"message": "Token deleted successfully"}


# PDF management endpoints
@app.post("/v0/orgs/{organization_id}/documents", tags=["documents"])
async def upload_document(
    organization_id: str,
    documents_upload: DocumentsUpload = Body(...),
    current_user: User = Depends(get_org_user)
):
    """Upload one or more documents"""
    logger.debug(f"upload_document(): documents: {[doc.name for doc in documents_upload.documents]}")
    documents = []

    # Validate all tag IDs first
    all_tag_ids = set()
    for document in documents_upload.documents:
        all_tag_ids.update(document.tag_ids)

    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)
    
    if all_tag_ids:
        # Check if all tags exist and belong to the organization
        tags_cursor = db.tags.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in all_tag_ids]},
            "organization_id": organization_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = all_tag_ids - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )

    for document in documents_upload.documents:
        try:
            mime_type = get_mime_type(document.name)
            ext = os.path.splitext(document.name)[1].lower()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        content = decode_base64_content(document.content)
        document_id = ad.common.create_id()
        mongo_file_name = f"{document_id}{ext}"

        metadata = {
            "document_id": document_id,
            "type": mime_type,
            "size": len(content),
            "user_file_name": document.name
        }

        # Save the document to mongodb
        await ad.common.save_file_async(analytiq_client,
                                        file_name=mongo_file_name,
                                        blob=content,
                                        metadata=metadata)

        if mime_type == "application/pdf":
            pdf_id = document_id
            pdf_file_name = mongo_file_name
        else:
            # Convert to PDF and save
            pdf_blob = ad.common.file.convert_to_pdf(content, ext)  # You will implement this function
            pdf_id = ad.common.create_id()
            pdf_file_name = f"{pdf_id}.pdf"
            await ad.common.save_file_async(analytiq_client, pdf_file_name, pdf_blob, metadata)

        document_metadata = {
            "_id": ObjectId(document_id),
            "user_file_name": document.name,
            "mongo_file_name": mongo_file_name,
            "document_id": document_id,
            "pdf_id": pdf_id,
            "pdf_file_name": pdf_file_name,
            "upload_date": datetime.now(UTC),
            "uploaded_by": current_user.user_name,
            "state": ad.common.doc.DOCUMENT_STATE_UPLOADED,
            "tag_ids": document.tag_ids,
            "metadata": document.metadata,
            "organization_id": organization_id
        }
        
        await ad.common.save_doc(analytiq_client, document_metadata)
        documents.append({
            "document_name": document.name,
            "document_id": document_id,
            "tag_ids": document.tag_ids,
            "metadata": document.metadata
        })

        # Post a message to the ocr job queue
        msg = {"document_id": document_id}
        await ad.queue.send_msg(analytiq_client, "ocr", msg=msg)
    
    return {"documents": documents}

@app.put("/v0/orgs/{organization_id}/documents/{document_id}", tags=["documents"])
async def update_document(
    organization_id: str,
    document_id: str,
    update: DocumentUpdate,
    current_user: User = Depends(get_org_user)
):
    """Update a document"""
    logger.info(f"Updating document {document_id} with data: {update}")
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)

    # Validate the document exists and belongs to the organization
    document = await db.docs.find_one({
        "_id": ObjectId(document_id),
        "organization_id": organization_id
    })
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Validate all tag IDs
    if update.tag_ids is not None:
        tags_cursor = db.tags.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in update.tag_ids]},
            "organization_id": organization_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = set(update.tag_ids) - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )
    
    # Prepare update dictionary
    update_dict = {}
    if update.tag_ids is not None:
        update_dict["tag_ids"] = update.tag_ids
    
    # Add document_name to update if provided
    if update.document_name is not None:
        update_dict["user_file_name"] = update.document_name
    
    # Add metadata to update if provided
    if update.metadata is not None:
        update_dict["metadata"] = update.metadata
    
    # Only proceed if there's something to update
    if not update_dict:
        return {"message": "No updates provided"}

    # Update the document
    updated_doc = await db.docs.find_one_and_update(
        {
            "_id": ObjectId(document_id),
            "organization_id": organization_id
        },
        {"$set": update_dict},
        return_document=True
    )

    if not updated_doc:
        raise HTTPException(
            status_code=404,
            detail="Document not found"
        )

    return {"message": "Document updated successfully"}


@app.get("/v0/orgs/{organization_id}/documents", response_model=ListDocumentsResponse, tags=["documents"])
async def list_documents(
    organization_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    tag_ids: str = Query(None, description="Comma-separated list of tag IDs"),
    name_search: str = Query(None, description="Search term for document names"),
    metadata_search: str = Query(None, description="Metadata search as key=value pairs, comma-separated (e.g., 'author=John,type=invoice'). Special characters in keys/values are URL-encoded automatically."),
    current_user: User = Depends(get_org_user)
):
    """List documents within an organization"""
    # Get analytiq client
    analytiq_client = ad.common.get_analytiq_client()
    
    tag_id_list = [tid.strip() for tid in tag_ids.split(",")] if tag_ids else None
    
    # Parse metadata search parameters
    metadata_search_dict = None
    if metadata_search:
        from urllib.parse import unquote
        metadata_search_dict = {}
        for pair in metadata_search.split(","):
            # Only strip leading whitespace to handle spacing around commas
            # Don't strip trailing whitespace as it might be part of the search value
            pair = pair.lstrip()
            # URL decode the pair first to handle encoded = signs
            decoded_pair = unquote(pair)
            if "=" in decoded_pair:
                key, value = decoded_pair.split("=", 1)
                # Strip whitespace from key but preserve value as-is
                metadata_search_dict[key.strip()] = value
    
    docs, total_count = await ad.common.list_docs(
        analytiq_client,
        organization_id=organization_id,
        skip=skip,
        limit=limit,
        tag_ids=tag_id_list,
        name_search=name_search,
        metadata_search=metadata_search_dict
    )
    
    return ListDocumentsResponse(
        documents=[
            DocumentMetadata(
                id=str(doc["_id"]),
                pdf_id=doc.get("pdf_id", doc.get("document_id", str(doc["_id"]))),  # fallback for old docs
                document_name=doc.get("user_file_name", doc.get("document_name", "")),
                upload_date=doc["upload_date"].replace(tzinfo=UTC).isoformat() if isinstance(doc["upload_date"], datetime) else doc["upload_date"],
                uploaded_by=doc.get("uploaded_by", ""),
                state=doc.get("state", ""),
                tag_ids=doc.get("tag_ids", []),
                metadata=doc.get("metadata", {}),
                # Optionally add pdf_file_name if you want to expose it
            )
            for doc in docs
        ],
        total_count=total_count,
        skip=skip
    )

@app.get("/v0/orgs/{organization_id}/documents/{document_id}", response_model=DocumentResponse, tags=["documents"])
async def get_document(
    organization_id: str,
    document_id: str,
    file_type: str = Query(default="original", 
                           enum=["original", "pdf"], 
                           description="Which file to retrieve: 'original' or 'pdf'"),
    current_user: User = Depends(get_org_user)
):
    """Get a document (original or associated PDF)"""
    logger.debug(f"get_document() start: document_id: {document_id}, file_type: {file_type}")
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)

    # Get document with organization scope
    document = await db.docs.find_one({
        "_id": ObjectId(document_id),
        "organization_id": organization_id
    })
    
    if not document:
        logger.debug(f"get_document() document not found: {document}")
        raise HTTPException(status_code=404, detail="Document not found")
        
    logger.debug(f"get_document() found document: {document}")

    # Decide which file to return
    if file_type == "pdf":
        file_name = document.get("pdf_file_name", document.get("mongo_file_name"))
    else:
        file_name = document.get("mongo_file_name")

    # Get the file from mongodb
    file = await ad.common.get_file_async(analytiq_client, file_name)
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    logger.debug(f"get_document() got file: {file_name}")

    # Determine MIME type for the file being returned
    # Prefer stored metadata.type; fall back to inferring from original name
    try:
        returned_mime = file["metadata"].get("type")
    except Exception:
        returned_mime = None
    if not returned_mime:
        try:
            returned_mime = get_mime_type(document["user_file_name"])
        except Exception:
            returned_mime = None

    # Return flattened response
    return DocumentResponse(
        id=str(document["_id"]),
        pdf_id=document.get("pdf_id", document["document_id"]),
        document_name=document["user_file_name"],
        upload_date=document["upload_date"].replace(tzinfo=UTC),
        uploaded_by=document["uploaded_by"],
        state=document.get("state", ""),
        tag_ids=document.get("tag_ids", []),
        type=returned_mime,
        metadata=document.get("metadata", {}),
        content=base64.b64encode(file["blob"]).decode('utf-8')
    )

@app.delete("/v0/orgs/{organization_id}/documents/{document_id}", tags=["documents"])
async def delete_document(
    organization_id: str,
    document_id: str,
    current_user: User = Depends(get_org_user)
):
    """Delete a document"""
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)

    # Get document with organization scope
    document = await db.docs.find_one({
        "_id": ObjectId(document_id),
        "organization_id": organization_id
    })
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if "mongo_file_name" not in document:
        raise HTTPException(
            status_code=500, 
            detail="Document metadata is corrupted: missing mongo_file_name"
        )

    # Delete the original file
    await ad.common.delete_file_async(analytiq_client, file_name=document["mongo_file_name"])

    # Delete the associated PDF if it's different
    pdf_file_name = document.get("pdf_file_name")
    if pdf_file_name and pdf_file_name != document["mongo_file_name"]:
        await ad.common.delete_file_async(analytiq_client, file_name=pdf_file_name)

    await ad.common.delete_doc(analytiq_client, document_id, organization_id)
    
    return {"message": "Document deleted successfully"}

@app.get("/v0/orgs/{organization_id}/ocr/download/blocks/{document_id}", tags=["ocr"])
async def download_ocr_blocks(
    organization_id: str,
    document_id: str,
    current_user: User = Depends(get_org_user)
):
    """Download OCR blocks for a document"""
    logger.debug(f"download_ocr_blocks() start: document_id: {document_id}")
    analytiq_client = ad.common.get_analytiq_client()

    document = await ad.common.get_doc(analytiq_client, document_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    file_name = document.get("user_file_name", "")
    if not ad.common.doc.ocr_supported(file_name):
        raise HTTPException(status_code=404, detail="OCR not supported for this document extension")

    # Get the OCR JSON data from mongodb
    ocr_json = await ad.common.get_ocr_json(analytiq_client, document_id)
    if ocr_json is None:
        raise HTTPException(status_code=404, detail="OCR data not found")
    
    return JSONResponse(content=ocr_json)

@app.get("/v0/orgs/{organization_id}/ocr/download/text/{document_id}", response_model=str, tags=["ocr"])
async def download_ocr_text(
    organization_id: str,
    document_id: str,
    page_num: Optional[int] = Query(None, description="Specific page number to retrieve"),
    current_user: User = Depends(get_org_user)
):
    """Download OCR text for a document"""
    logger.debug(f"download_ocr_text() start: document_id: {document_id}, page_num: {page_num}")
    
    analytiq_client = ad.common.get_analytiq_client()
    document = await ad.common.get_doc(analytiq_client, document_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    file_name = document.get("user_file_name", "")
    if not ad.common.doc.ocr_supported(file_name):
        raise HTTPException(status_code=404, detail="OCR not supported for this document extension")

    # Page number is 1-based, but the OCR text page_idx is 0-based
    page_idx = None
    if page_num is not None:
        page_idx = page_num - 1

    # Get the OCR text data from mongodb
    text = await ad.common.get_ocr_text(analytiq_client, document_id, page_idx)
    if text is None:
        raise HTTPException(status_code=404, detail="OCR text not found")
    
    return Response(content=text, media_type="text/plain")

@app.get("/v0/orgs/{organization_id}/ocr/download/metadata/{document_id}", response_model=GetOCRMetadataResponse, tags=["ocr"])
async def get_ocr_metadata(
    organization_id: str,
    document_id: str,
    current_user: User = Depends(get_org_user)
):
    """Get OCR metadata for a document"""
    logger.debug(f"get_ocr_metadata() start: document_id: {document_id}")
    
    analytiq_client = ad.common.get_analytiq_client()
    document = await ad.common.get_doc(analytiq_client, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    file_name = document.get("user_file_name", "")
    if not ad.common.doc.ocr_supported(file_name):
        raise HTTPException(status_code=404, detail="OCR not supported for this document extension")

    # Get the OCR metadata from mongodb
    metadata = await ad.common.get_ocr_metadata(analytiq_client, document_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="OCR metadata not found")
    
    return GetOCRMetadataResponse(
        n_pages=metadata["n_pages"],
        ocr_date=metadata["ocr_date"].isoformat()
    )

# OpenTelemetry Endpoints
@app.post("/v0/orgs/{organization_id}/telemetry/traces", tags=["telemetry"])
async def upload_telemetry_traces(
    organization_id: str,
    traces_upload: TelemetryTracesUpload = Body(...),
    current_user: User = Depends(get_org_user)
):
    """Upload OpenTelemetry traces"""
    logger.debug(f"upload_telemetry_traces(): traces: {len(traces_upload.traces)}")
    
    # Validate all tag IDs first
    all_tag_ids = set()
    for trace in traces_upload.traces:
        all_tag_ids.update(trace.tag_ids)

    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)
    
    if all_tag_ids:
        # Check if all tags exist and belong to the organization
        tags_cursor = db.tags.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in all_tag_ids]},
            "organization_id": organization_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = all_tag_ids - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )

    uploaded_traces = []
    
    for trace in traces_upload.traces:
        trace_id = ad.common.create_id()
        
        # Calculate span count from resource_spans
        span_count = 0
        for resource_span in trace.resource_spans:
            if "scope_spans" in resource_span:
                for scope_span in resource_span["scope_spans"]:
                    if "spans" in scope_span:
                        span_count += len(scope_span["spans"])
        
        trace_metadata = {
            "_id": ObjectId(trace_id),
            "trace_id": trace_id,
            "resource_spans": trace.resource_spans,
            "span_count": span_count,
            "upload_date": datetime.now(UTC),
            "uploaded_by": current_user.user_name,
            "tag_ids": trace.tag_ids,
            "metadata": trace.metadata,
            "organization_id": organization_id
        }
        
        await db.telemetry_traces.insert_one(trace_metadata)
        uploaded_traces.append({
            "trace_id": trace_id,
            "span_count": span_count,
            "tag_ids": trace.tag_ids,
            "metadata": trace.metadata
        })
    
    return {"traces": uploaded_traces}

@app.post("/v0/orgs/{organization_id}/telemetry/metrics", tags=["telemetry"])
async def upload_telemetry_metrics(
    organization_id: str,
    metrics_upload: TelemetryMetricsUpload = Body(...),
    current_user: User = Depends(get_org_user)
):
    """Upload OpenTelemetry metrics"""
    logger.debug(f"upload_telemetry_metrics(): metrics: {len(metrics_upload.metrics)}")
    
    # Validate all tag IDs first
    all_tag_ids = set()
    for metric in metrics_upload.metrics:
        all_tag_ids.update(metric.tag_ids)

    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)
    
    if all_tag_ids:
        # Check if all tags exist and belong to the organization
        tags_cursor = db.tags.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in all_tag_ids]},
            "organization_id": organization_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = all_tag_ids - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )

    uploaded_metrics = []
    
    for metric in metrics_upload.metrics:
        metric_id = ad.common.create_id()
        
        metric_metadata = {
            "_id": ObjectId(metric_id),
            "metric_id": metric_id,
            "name": metric.name,
            "description": metric.description,
            "unit": metric.unit,
            "type": metric.type,
            "data_points": metric.data_points,
            "data_point_count": len(metric.data_points),
            "resource": metric.resource,
            "upload_date": datetime.now(UTC),
            "uploaded_by": current_user.user_name,
            "tag_ids": metric.tag_ids,
            "metadata": metric.metadata,
            "organization_id": organization_id
        }
        
        await db.telemetry_metrics.insert_one(metric_metadata)
        uploaded_metrics.append({
            "metric_id": metric_id,
            "name": metric.name,
            "type": metric.type,
            "data_point_count": len(metric.data_points),
            "tag_ids": metric.tag_ids,
            "metadata": metric.metadata
        })
    
    return {"metrics": uploaded_metrics}

@app.post("/v0/orgs/{organization_id}/telemetry/logs", tags=["telemetry"])
async def upload_telemetry_logs(
    organization_id: str,
    logs_upload: TelemetryLogsUpload = Body(...),
    current_user: User = Depends(get_org_user)
):
    """Upload OpenTelemetry logs"""
    logger.debug(f"upload_telemetry_logs(): logs: {len(logs_upload.logs)}")
    
    # Validate all tag IDs first
    all_tag_ids = set()
    for log in logs_upload.logs:
        all_tag_ids.update(log.tag_ids)

    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)
    
    if all_tag_ids:
        # Check if all tags exist and belong to the organization
        tags_cursor = db.tags.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in all_tag_ids]},
            "organization_id": organization_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = all_tag_ids - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )

    uploaded_logs = []
    
    for log in logs_upload.logs:
        log_id = ad.common.create_id()
        
        log_metadata = {
            "_id": ObjectId(log_id),
            "log_id": log_id,
            "timestamp": log.timestamp,
            "severity": log.severity,
            "body": log.body,
            "attributes": log.attributes,
            "resource": log.resource,
            "trace_id": log.trace_id,
            "span_id": log.span_id,
            "upload_date": datetime.now(UTC),
            "uploaded_by": current_user.user_name,
            "tag_ids": log.tag_ids,
            "metadata": log.metadata,
            "organization_id": organization_id
        }
        
        await db.telemetry_logs.insert_one(log_metadata)
        uploaded_logs.append({
            "log_id": log_id,
            "timestamp": log.timestamp,
            "severity": log.severity,
            "body": log.body,
            "tag_ids": log.tag_ids,
            "metadata": log.metadata
        })
    
    return {"logs": uploaded_logs}

@app.get("/v0/orgs/{organization_id}/telemetry/traces", response_model=ListTelemetryTracesResponse, tags=["telemetry"])
async def list_telemetry_traces(
    organization_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    tag_ids: str = Query(None, description="Comma-separated list of tag IDs"),
    name_search: str = Query(None, description="Search term for trace names"),
    current_user: User = Depends(get_org_user)
):
    """List OpenTelemetry traces"""
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)
    
    # Build query
    query = {"organization_id": organization_id}
    
    if tag_ids:
        tag_id_list = [tag_id.strip() for tag_id in tag_ids.split(",")]
        query["tag_ids"] = {"$in": tag_id_list}
    
    # Get total count
    total = await db.telemetry_traces.count_documents(query)
    
    # Get traces
    cursor = db.telemetry_traces.find(query).skip(skip).limit(limit).sort("upload_date", -1)
    traces = []
    
    async for trace in cursor:
        traces.append(TelemetryTraceResponse(
            trace_id=trace["trace_id"],
            span_count=trace["span_count"],
            upload_date=trace["upload_date"],
            uploaded_by=trace["uploaded_by"],
            tag_ids=trace["tag_ids"],
            metadata=trace["metadata"]
        ))
    
    return ListTelemetryTracesResponse(
        traces=traces,
        total=total,
        skip=skip,
        limit=limit
    )

@app.get("/v0/orgs/{organization_id}/telemetry/metrics", response_model=ListTelemetryMetricsResponse, tags=["telemetry"])
async def list_telemetry_metrics(
    organization_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    tag_ids: str = Query(None, description="Comma-separated list of tag IDs"),
    name_search: str = Query(None, description="Search term for metric names"),
    current_user: User = Depends(get_org_user)
):
    """List OpenTelemetry metrics"""
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)
    
    # Build query
    query = {"organization_id": organization_id}
    
    if tag_ids:
        tag_id_list = [tag_id.strip() for tag_id in tag_ids.split(",")]
        query["tag_ids"] = {"$in": tag_id_list}
    
    if name_search:
        query["name"] = {"$regex": name_search, "$options": "i"}
    
    # Get total count
    total = await db.telemetry_metrics.count_documents(query)
    
    # Get metrics
    cursor = db.telemetry_metrics.find(query).skip(skip).limit(limit).sort("upload_date", -1)
    metrics = []
    
    async for metric in cursor:
        metrics.append(TelemetryMetricResponse(
            metric_id=metric["metric_id"],
            name=metric["name"],
            type=metric["type"],
            data_point_count=metric["data_point_count"],
            upload_date=metric["upload_date"],
            uploaded_by=metric["uploaded_by"],
            tag_ids=metric["tag_ids"],
            metadata=metric["metadata"]
        ))
    
    return ListTelemetryMetricsResponse(
        metrics=metrics,
        total=total,
        skip=skip,
        limit=limit
    )

@app.get("/v0/orgs/{organization_id}/telemetry/logs", response_model=ListTelemetryLogsResponse, tags=["telemetry"])
async def list_telemetry_logs(
    organization_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    tag_ids: str = Query(None, description="Comma-separated list of tag IDs"),
    severity: str = Query(None, description="Filter by log severity"),
    current_user: User = Depends(get_org_user)
):
    """List OpenTelemetry logs"""
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)
    
    # Build query
    query = {"organization_id": organization_id}
    
    if tag_ids:
        tag_id_list = [tag_id.strip() for tag_id in tag_ids.split(",")]
        query["tag_ids"] = {"$in": tag_id_list}
    
    if severity:
        query["severity"] = severity
    
    # Get total count
    total = await db.telemetry_logs.count_documents(query)
    
    # Get logs
    cursor = db.telemetry_logs.find(query).skip(skip).limit(limit).sort("timestamp", -1)
    logs = []
    
    async for log in cursor:
        logs.append(TelemetryLogResponse(
            log_id=log["log_id"],
            timestamp=log["timestamp"],
            severity=log["severity"],
            body=log["body"],
            upload_date=log["upload_date"],
            uploaded_by=log["uploaded_by"],
            tag_ids=log["tag_ids"],
            metadata=log["metadata"]
        ))
    
    return ListTelemetryLogsResponse(
        logs=logs,
        total=total,
        skip=skip,
        limit=limit
    )


# OTLP Management Endpoints
@app.post("/v0/orgs/{organization_id}/otlp/enable", tags=["otlp"])
async def enable_otlp_for_organization(
    organization_id: str,
    current_user: User = Depends(get_org_user)
):
    """Enable OTLP gRPC endpoints for an organization"""
    try:
        add_organization_to_otlp(organization_id)
        logger.info(f"Enabled OTLP for organization: {organization_id}")
        return {"status": "success", "message": f"OTLP enabled for organization {organization_id}"}
    except Exception as e:
        logger.error(f"Failed to enable OTLP for organization {organization_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to enable OTLP: {str(e)}")

@app.delete("/v0/orgs/{organization_id}/otlp/disable", tags=["otlp"])
async def disable_otlp_for_organization(
    organization_id: str,
    current_user: User = Depends(get_org_user)
):
    """Disable OTLP gRPC endpoints for an organization"""
    try:
        remove_organization_from_otlp(organization_id)
        logger.info(f"Disabled OTLP for organization: {organization_id}")
        return {"status": "success", "message": f"OTLP disabled for organization {organization_id}"}
    except Exception as e:
        logger.error(f"Failed to disable OTLP for organization {organization_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to disable OTLP: {str(e)}")

@app.get("/v0/orgs/{organization_id}/otlp/status", tags=["otlp"])
async def get_otlp_status(
    organization_id: str,
    current_user: User = Depends(get_org_user)
):
    """Get OTLP status for an organization"""
    from .otlp_server import _organization_services
    
    is_enabled = organization_id in _organization_services
    return {
        "organization_id": organization_id,
        "otlp_enabled": is_enabled,
        "endpoint": f"grpc://localhost:4317" if is_enabled else None,
        "protocol": "grpc",
        "headers": {
            "organization-id": organization_id
        }
    }

# LLM Run Endpoints
@app.post("/v0/orgs/{organization_id}/llm/run/{document_id}", response_model=LLMRunResponse, tags=["llm"])
async def run_llm_analysis(
    organization_id: str,
    document_id: str,
    prompt_revid: str = Query(default="default", description="The prompt revision ID to use"),
    force: bool = Query(default=False, description="Force new run even if result exists"),
    current_user: User = Depends(get_org_user)
):
    """
    Run LLM on a document, with optional force refresh.
    """
    logger.info(f"run_llm_analysis(): doc_id/prompt_revid {document_id}/{prompt_revid}, force: {force}")
    analytiq_client = ad.common.get_analytiq_client()
    
    # Verify document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Verify OCR is complete only if the document requires OCR
    file_name = document.get("user_file_name", "")
    if ad.common.doc.ocr_supported(file_name):
        ocr_metadata = await ad.common.get_ocr_metadata(analytiq_client, document_id)
        if ocr_metadata is None:
            raise HTTPException(status_code=404, detail="OCR metadata not found")

    try:
        result = await ad.llm.run_llm(
            analytiq_client,
            document_id=document_id,
            prompt_revid=prompt_revid,
            force=force
        )
        
        # Update state to LLM completed
        await ad.common.doc.update_doc_state(analytiq_client, document_id, ad.common.doc.DOCUMENT_STATE_LLM_COMPLETED)
        
        return LLMRunResponse(
            status="success",
            result=result
        )
        
    except SPUCreditException as e:
        logger.warning(f"{document_id}/{prompt_revid}: SPU credit exhausted in LLM run: {str(e)}")
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient SPU credits: {str(e)}"
        )
    except Exception as e:
        logger.error(f"{document_id}/{prompt_revid}: Error in LLM run: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing document: {str(e)}"
        )

# Admin-only LLM testing endpoint - Account level
@app.post("/v0/account/llm/run", tags=["account/llm"])
async def run_llm_chat_account(
    request: LLMPromptRequest,
    current_user: User = Depends(get_admin_user)
):
    """
    Test LLM with arbitrary prompt (admin only) - Account level.
    Supports both streaming and non-streaming responses.
    """
    return await ad.llm.run_llm_chat(request, current_user)

# Admin-only LLM testing endpoint - Organization level  
@app.post("/v0/orgs/{organization_id}/llm/run", tags=["llm"])
async def run_llm_chat_org(
    organization_id: str,
    request: LLMPromptRequest,
    current_user: User = Depends(get_admin_user)
):
    """
    Test LLM with arbitrary prompt (admin or org admin) - Organization level.
    Supports both streaming and non-streaming responses.
    """
    return await ad.llm.run_llm_chat(request, current_user)

@app.get("/v0/orgs/{organization_id}/llm/result/{document_id}", response_model=LLMResult, tags=["llm"])
async def get_llm_result(
    organization_id: str,
    document_id: str,
    prompt_revid: str = Query(default="default", description="The prompt revision ID to retrieve"),
    fallback: bool = Query(default=False, description="Whether to fallback to the most recent available prompt revision result"),
    current_user: User = Depends(get_org_user)
):
    """
    Retrieve existing LLM results for a document.
    """
    logger.debug(f"get_llm_result() start: document_id: {document_id}, prompt_revid: {prompt_revid}")
    analytiq_client = ad.common.get_analytiq_client()
    
    # Verify document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id, organization_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    llm_result = await ad.llm.get_llm_result(analytiq_client, document_id, prompt_revid, fallback)
    if not llm_result:
        raise HTTPException(
            status_code=404,
            detail=f"LLM result not found for document_id: {document_id} prompt_revid: {prompt_revid} fallback: {fallback}"
        )
    
    return llm_result

@app.put("/v0/orgs/{organization_id}/llm/result/{document_id}", response_model=LLMResult, tags=["llm"])
async def update_llm_result(
    organization_id: str,
    document_id: str,
    prompt_revid: str = Query(..., description="The prompt revision ID to update"),
    update: UpdateLLMResultRequest = Body(..., description="The update request"),
    current_user: User = Depends(get_org_user)
):
    """
    Update LLM results with user edits and verification status.
    """
    analytiq_client = ad.common.get_analytiq_client()
    
    # Verify document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id, organization_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    try:
        await ad.llm.update_llm_result(
            analytiq_client,
            document_id=document_id,
            prompt_revid=prompt_revid,
            updated_llm_result=update.updated_llm_result,
            is_verified=update.is_verified
        )
        
        return await ad.llm.get_llm_result(analytiq_client, document_id, prompt_revid)
        
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating LLM result: {str(e)}"
        )

@app.delete("/v0/orgs/{organization_id}/llm/result/{document_id}", tags=["llm"])
async def delete_llm_result(
    organization_id: str,
    document_id: str,
    prompt_revid: str = Query(..., description="The prompt revision ID to delete"),
    current_user: User = Depends(get_org_user)
):
    """
    Delete LLM results for a specific document and prompt.
    """
    logger.debug(f"delete_llm_result() start: document_id: {document_id}, prompt_revid: {prompt_revid}")
    analytiq_client = ad.common.get_analytiq_client()
    # Verify document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id, organization_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    deleted = await ad.llm.delete_llm_result(analytiq_client, document_id, prompt_revid)
    
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"LLM result not found for document_id: {document_id} and prompt_revid: {prompt_revid}"
        )
    
    return {"status": "success", "message": "LLM result deleted"}

@app.get("/v0/orgs/{organization_id}/llm/results/{document_id}/download", tags=["llm"])
async def download_all_llm_results(
    organization_id: str,
    document_id: str,
    current_user: User = Depends(get_org_user)
):
    """
    Download all LLM results for a document as a JSON file.
    """
    logger.debug(f"download_all_llm_results() start: document_id: {document_id}")
    analytiq_client = ad.common.get_analytiq_client()
    
    # Verify document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id, organization_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get all LLM results for this document
    db = ad.common.get_async_db(analytiq_client)
    llm_results = await db.llm_runs.find({
        "document_id": document_id
    }).to_list(None)
    
    if not llm_results:
        raise HTTPException(
            status_code=404,
            detail="No LLM results found for this document"
        )
    
    # Get prompts information for context
    prompt_ids = list(set(result["prompt_revid"] for result in llm_results))
    # Filter out non-ObjectId prompt IDs (like "default")
    valid_prompt_ids = []
    for pid in prompt_ids:
        try:
            valid_prompt_ids.append(ObjectId(pid))
        except:
            # Skip invalid ObjectIds like "default"
            continue
    
    prompts = await db.prompt_revisions.find({
        "_id": {"$in": valid_prompt_ids}
    }).to_list(None)
    
    prompt_map = {str(p["_id"]): p for p in prompts}
    
    # Prepare download data
    download_data = {
        "document_id": document_id,
        "organization_id": organization_id,
        "document_name": document.get("user_file_name", "Unknown"),
        "extraction_date": datetime.now(UTC).isoformat(),
        "results": []
    }
    
    for result in llm_results:
        prompt_info = prompt_map.get(result["prompt_revid"], {})
        # Handle special case for "default" prompt
        if result["prompt_revid"] == "default":
            prompt_name = "Default Prompt"
        else:
            prompt_name = prompt_info.get("content", "Unknown")[:100] + "..." if len(prompt_info.get("content", "")) > 100 else prompt_info.get("content", "Unknown")
        
        download_data["results"].append({
            "prompt_revid": result["prompt_revid"],
            "prompt_name": prompt_name,
            "prompt_version": result.get("prompt_version", 0),
            "extraction_result": result["updated_llm_result"],
            "metadata": {
                "created_at": result["created_at"].isoformat() if result.get("created_at") else None,
                "updated_at": result["updated_at"].isoformat() if result.get("updated_at") else None,
                "is_edited": result.get("is_edited", False),
                "is_verified": result.get("is_verified", False)
            }
        })
    
    # Create JSON response
    json_content = json.dumps(download_data, indent=2, default=str)
    
    # Return as downloadable file
    return Response(
        content=json_content,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=extractions_{document_id}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
        }
    )

# Add this helper function near the top of the file with other functions
async def get_schema_id_and_version(schema_id: Optional[str] = None) -> tuple[str, int]:
    """
    Get the next version for an existing schema or create a new schema identifier.
    
    Args:
        schema_id: Existing schema ID, or None to create a new one
        
    Returns:
        Tuple of (schema_id, schema_version)
    """
    db = ad.common.get_async_db()

    if schema_id is None:
        # Insert a placeholder document to get MongoDB-generated ID
        result = await db.schemas.insert_one({
            "schema_version": 1
        })
        
        # Use the MongoDB-assigned _id as our schema_id
        schema_id = str(result.inserted_id)
        schema_version = 1
    else:
        # Get the next version for an existing schema
        result = await db.schemas.find_one_and_update(
            {"_id": ObjectId(schema_id)},
            {"$inc": {"schema_version": 1}},
            upsert=True,
            return_document=True
        )
        schema_version = result["schema_version"]
    
    return schema_id, schema_version

# Schema management endpoints
@app.post("/v0/orgs/{organization_id}/schemas", response_model=Schema, tags=["schemas"])
async def create_schema(
    organization_id: str,
    schema: SchemaConfig,
    current_user: User = Depends(get_org_user)
):
    """Create a schema"""
    logger.info(f"create_schema() start: organization_id: {organization_id}, schema: {schema}")
    db = ad.common.get_async_db()

    # Check if schema with this name already exists (case-insensitive)
    existing_schema = await db.schemas.find_one({
        "name": {"$regex": f"^{schema.name}$", "$options": "i"},
        "organization_id": organization_id
    })

    # Generate schema_id and version
    if existing_schema:
        schema_id, new_schema_version = await get_schema_id_and_version(str(existing_schema["_id"]))
    else:
        # Generate a new schema_id when creating a new schema
        schema_id, new_schema_version = await get_schema_id_and_version(None)
    
    # Update the schemas collection with name and organization_id
    await db.schemas.update_one(
        {"_id": ObjectId(schema_id)},
        {"$set": {
            "name": schema.name,
            "organization_id": organization_id
        }},
        upsert=True
    )
    
    # Create schema document for schema_revisions
    schema_dict = {
        "schema_id": schema_id,
        "response_format": schema.response_format.model_dump(),
        "schema_version": new_schema_version,
        "created_at": datetime.now(UTC),
        "created_by": current_user.user_id
    }
    
    # Insert into MongoDB
    result = await db.schema_revisions.insert_one(schema_dict)
    
    # Return complete schema
    schema_dict["name"] = schema.name
    schema_dict["schema_revid"] = str(result.inserted_id)
    return Schema(**schema_dict)

@app.get("/v0/orgs/{organization_id}/schemas", response_model=ListSchemasResponse, tags=["schemas"])
async def list_schemas(
    organization_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    name_search: str = Query(None, description="Search term for schema names"),
    current_user: User = Depends(get_org_user)
):
    """List latest schema revisions within an organization"""
    logger.info(f"list_schemas() start: organization_id: {organization_id}, skip: {skip}, limit: {limit}")
    db = ad.common.get_async_db()
    
    # First, get schemas that belong to the organization
    # Build base query for schemas in org
    schemas_query = {"organization_id": organization_id}
    # Optional name search (case-insensitive)
    if name_search:
        schemas_query["name"] = {"$regex": name_search, "$options": "i"}

    org_schemas = await db.schemas.find(schemas_query).to_list(None)
    
    if not org_schemas:
        return ListSchemasResponse(schemas=[], total_count=0, skip=skip)
    
    # Extract schema IDs (restricted to name_search if provided)
    schema_ids = [schema["_id"] for schema in org_schemas]
    schema_id_to_name = {str(schema["_id"]): schema["name"] for schema in org_schemas}
    
    # Build pipeline for schema_revisions
    pipeline = [
        {
            "$match": {"schema_id": {"$in": [str(sid) for sid in schema_ids]}}
        },
        {
            "$sort": {"_id": -1}
        },
        {
            "$group": {
                "_id": "$schema_id",
                "doc": {"$first": "$$ROOT"}
            }
        },
        {
            "$replaceRoot": {"newRoot": "$doc"}
        },
        {
            "$sort": {"_id": -1}
        },
        {
            "$facet": {
                "total": [{"$count": "count"}],
                "schemas": [
                    {"$skip": skip},
                    {"$limit": limit}
                ]
            }
        }
    ]
    
    result = await db.schema_revisions.aggregate(pipeline).to_list(length=1)
    result = result[0]
    
    total_count = result["total"][0]["count"] if result["total"] else 0
    schemas = result["schemas"]
    
    # Convert _id to id in each schema and add name from schemas collection
    for schema in schemas:
        schema['schema_revid'] = str(schema.pop('_id'))
        schema['name'] = schema_id_to_name.get(schema['schema_id'], "Unknown")
    
    return ListSchemasResponse(
        schemas=schemas,
        total_count=total_count,
        skip=skip
    )

@app.get("/v0/orgs/{organization_id}/schemas/{schema_revid}", response_model=Schema, tags=["schemas"])
async def get_schema(
    organization_id: str,
    schema_revid: str,
    current_user: User = Depends(get_org_user)
):
    """Get a schema revision"""
    logger.info(f"get_schema() start: organization_id: {organization_id}, schema_revid: {schema_revid}")
    db = ad.common.get_async_db()
    
    # Get the schema revision
    revision = await db.schema_revisions.find_one({
        "_id": ObjectId(schema_revid)
    })
    if not revision:
        raise HTTPException(status_code=404, detail="Schema not found")
    
    # Get the schema name and verify organization
    schema = await db.schemas.find_one({
        "_id": ObjectId(revision["schema_id"]),
        "organization_id": organization_id
    })
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found or not in this organization")
    
    # Combine the data
    revision['schema_revid'] = str(revision.pop('_id'))
    revision['name'] = schema['name']
    
    return Schema(**revision)

@app.put("/v0/orgs/{organization_id}/schemas/{schema_id}", response_model=Schema, tags=["schemas"])
async def update_schema(
    organization_id: str,
    schema_id: str,
    schema: SchemaConfig,
    current_user: User = Depends(get_org_user)
):
    """Update a schema"""
    logger.info(f"update_schema() start: organization_id: {organization_id}, schema_id: {schema_id}, schema: {schema}")
    
    db = ad.common.get_async_db()

    # Check if user is a member of the organization
    org = await db.organizations.find_one({"_id": ObjectId(organization_id)})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Check if user is a member of the organization
    if not any(member["user_id"] == current_user.user_id for member in org["members"]):
        raise HTTPException(status_code=403, detail="Not authorized to update schemas in this organization")

    # Get the existing schema and latest revision
    existing_schema = await db.schemas.find_one({"_id": ObjectId(schema_id)})
    if not existing_schema:
        raise HTTPException(status_code=404, detail="Schema not found")
    
    latest_schema_revision = await db.schema_revisions.find_one(
        {"schema_id": schema_id},
        sort=[("schema_version", -1)]
    )
    
    if not latest_schema_revision:
        raise HTTPException(status_code=404, detail="Schema revision not found")

    # Check if only the name has changed
    only_name_changed = (
        schema.name != existing_schema["name"] and
        schema.response_format.model_dump() == latest_schema_revision["response_format"]
    )
    
    if only_name_changed:
        # Update the name in the schemas collection
        result = await db.schemas.update_one(
            {"_id": ObjectId(schema_id)},
            {"$set": {"name": schema.name}}
        )
        
        if result.modified_count > 0:
            # Return the updated schema
            updated_revision = latest_schema_revision.copy()
            updated_revision["schema_revid"] = str(updated_revision.pop("_id"))
            updated_revision["name"] = schema.name
            return Schema(**updated_revision)
        else:
            raise HTTPException(status_code=500, detail="Failed to update schema name")
    
    # If other fields changed, create a new version
    # Get the next version number using the stable schema_id
    _, new_schema_version = await get_schema_id_and_version(schema_id)
    
    # Update the schemas collection if name changed
    if schema.name != existing_schema["name"]:
        await db.schemas.update_one(
            {"_id": ObjectId(schema_id)},
            {"$set": {"name": schema.name}}
        )
    
    # Create new version of the schema in schema_revisions
    new_schema = {
        "schema_id": schema_id,
        "response_format": schema.response_format.model_dump(),
        "schema_version": new_schema_version,
        "created_at": datetime.now(UTC),
        "created_by": current_user.user_id
    }
    
    # Insert new version
    result = await db.schema_revisions.insert_one(new_schema)
    
    # Return updated schema
    new_schema["schema_revid"] = str(result.inserted_id)
    new_schema["name"] = schema.name
    return Schema(**new_schema)

@app.delete("/v0/orgs/{organization_id}/schemas/{schema_id}", tags=["schemas"])
async def delete_schema(
    organization_id: str,
    schema_id: str,
    current_user: User = Depends(get_org_user)
):
    """Delete a schema"""
    logger.info(f"delete_schema() start: organization_id: {organization_id}, schema_id: {schema_id}")

    db = ad.common.get_async_db()
    
    # Get the schema and verify organization
    schema = await db.schemas.find_one({
        "_id": ObjectId(schema_id),
        "organization_id": organization_id
    })
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found or not in this organization")

    # Check for dependent prompts by schema_id
    dependent_prompts = await db.prompt_revisions.find({
        "schema_id": schema_id
    }).to_list(None)
    
    if dependent_prompts:
        # Get names from prompts collection
        prompt_names = {}
        for prompt_revision in dependent_prompts:
            prompt = await db.prompts.find_one({"_id": ObjectId(prompt_revision["prompt_id"])})
            if prompt:
                prompt_names[str(prompt_revision["_id"])] = prompt["name"]
        
        # Format the list of dependent prompts
        prompt_list = [
            {
                "name": prompt_names.get(str(p["_id"]), "Unknown"), 
                "schema_version": p["schema_version"]
            } 
            for p in dependent_prompts
        ]
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete schema because it has dependent prompts:{json.dumps(prompt_list)}"
        )
    
    # If no dependent prompts, proceed with deletion
    result = await db.schema_revisions.delete_many({
        "schema_id": schema_id
    })
    
    # Delete the schema entry
    await db.schemas.delete_one({"_id": ObjectId(schema_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No schema revisions found")
        
    return {"message": "Schema deleted successfully"}

@app.post("/v0/orgs/{organization_id}/schemas/{schema_revid}/validate", tags=["schemas"])
async def validate_against_schema(
    organization_id: str,
    schema_revid: str,
    data: dict = Body(...),
    current_user: User = Depends(get_org_user)
):
    """Validate data against a schema revision"""
    logger.info(f"validate_against_schema() start: organization_id: {organization_id}, schema_revid: {schema_revid}")
    
    db = ad.common.get_async_db()
    
    # Get the schema
    schema_doc = await db.schema_revisions.find_one({
        "_id": ObjectId(schema_revid),
    })
    
    if not schema_doc:
        raise HTTPException(status_code=404, detail="Schema not found")
    
    # Extract the JSON schema from the schema document
    try:
        json_schema = schema_doc["response_format"]["json_schema"]["schema"]
        
        # Get the data to validate
        if "data" not in data:
            raise HTTPException(status_code=400, detail="Request must include 'data' field")
        
        instance_data = data["data"]
        
        # Validate the data against the schema
        validator = Draft7Validator(json_schema)
        errors = list(validator.iter_errors(instance_data))
        
        if not errors:
            return {"valid": True}
        
        # Format validation errors
        formatted_errors = []
        for error in errors:
            formatted_errors.append({
                "path": ".".join(str(p) for p in error.path) if error.path else "",
                "message": error.message
            })
        
        return {
            "valid": False,
            "errors": formatted_errors
        }
    
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Invalid schema format: {str(e)}")
    except Exception as e:
        logger.error(f"Error validating data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error validating data: {str(e)}")


# Add this validation function
def validate_schema_fields(fields: list) -> tuple[bool, str]:
    field_names = [field.name.lower() for field in fields]
    seen = set()
    for name in field_names:
        if name in seen:
            return False, f"Duplicate field name: {name}"
        seen.add(name)
    return True, ""

async def get_prompt_id_and_version(prompt_id: Optional[str] = None) -> tuple[str, int]:
    """
    Get the next version for an existing prompt or create a new prompt identifier.
    
    Args:
        prompt_id: Existing prompt ID, or None to create a new one
        
    Returns:
        Tuple of (prompt_id, prompt_version)
    """
    db = ad.common.get_async_db()

    if prompt_id is None:
        # Insert a placeholder document to get MongoDB-generated ID
        result = await db.prompts.insert_one({
            "prompt_version": 1
        })
        
        # Use the MongoDB-assigned _id as our prompt_id
        prompt_id = str(result.inserted_id)
        prompt_version = 1
    else:
        # Get the next version for an existing prompt
        result = await db.prompts.find_one_and_update(
            {"_id": ObjectId(prompt_id)},
            {"$inc": {"prompt_version": 1}},
            upsert=True,
            return_document=True
        )
        prompt_version = result["prompt_version"]
    
    return prompt_id, prompt_version

async def validate_and_resolve_schema(prompt: PromptConfig) -> Optional[dict]:
    """
    Validate and resolve schema information for a prompt.
    
    If schema_id is provided:
    - If schema_version is also provided, validates that specific version exists
    - If schema_version is None, auto-fetches the latest version and updates the prompt
    
    Args:
        prompt: The prompt configuration to validate
        
    Returns:
        The schema document if found, None if no schema_id provided
        
    Raises:
        HTTPException: If schema_id is provided but schema not found
    """
    if not prompt.schema_id:
        return None
        
    db = ad.common.get_async_db()
    
    if prompt.schema_version:
        # Both schema_id and schema_version provided - verify specific version
        schema = await db.schema_revisions.find_one({
            "schema_id": prompt.schema_id,
            "schema_version": prompt.schema_version,
        })
        if not schema:
            raise HTTPException(
                status_code=404,
                detail=f"Schema with ID {prompt.schema_id} version {prompt.schema_version} not found"
            )
    else:
        # Only schema_id provided - auto-fetch latest version
        schema = await db.schema_revisions.find_one(
            {"schema_id": prompt.schema_id},
            sort=[("schema_version", -1)]  # Get highest version
        )
        if not schema:
            raise HTTPException(
                status_code=404,
                detail=f"Schema with ID {prompt.schema_id} not found"
            )
        # Update the prompt with the latest schema version
        prompt.schema_version = schema["schema_version"]
    
    return schema

# Prompt management endpoints
@app.post("/v0/orgs/{organization_id}/prompts", response_model=Prompt, tags=["prompts"])
async def create_prompt(
    organization_id: str,
    prompt: PromptConfig,
    current_user: User = Depends(get_org_user)
):
    """Create a prompt"""
    logger.info(f"create_prompt() start: organization_id: {organization_id}, prompt: {prompt}")
    db = ad.common.get_async_db()

    # Verify schema if specified
    schema = await validate_and_resolve_schema(prompt)

    # Validate model exists
    found = False
    for provider in await db.llm_providers.find({}).to_list(None):
        if prompt.model in provider["litellm_models_enabled"]:
            found = True
            break
    if not found:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model: {prompt.model}"
        )

    # Validate tag IDs if provided
    if prompt.tag_ids:
        tags_cursor = db.tags.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in prompt.tag_ids]},
            "organization_id": organization_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = set(prompt.tag_ids) - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )
        # Only set schema_id if schema exists and was verified above
        if prompt.schema_id and 'schema' in locals():
            prompt.schema_id = schema["schema_id"]

    prompt_name = prompt.name

    # Does the prompt name already exist?
    existing_prompt = await db.prompts.find_one({
        "name": prompt_name,
        "organization_id": organization_id
    })

    if existing_prompt:
        prompt_id, new_prompt_version = await get_prompt_id_and_version(existing_prompt["prompt_id"])
    else:
        prompt_id, new_prompt_version = await get_prompt_id_and_version(None)
    
    # Update the prompts collection with name and organization_id
    await db.prompts.update_one(
        {"_id": ObjectId(prompt_id)},
        {"$set": {
            "name": prompt.name,
            "organization_id": organization_id
        }},
        upsert=True
    )
    
    # Create prompt document for prompt_revisions
    prompt_dict = {
        "prompt_id": prompt_id,  # Add stable identifier
        "content": prompt.content,
        "schema_id": prompt.schema_id,
        "schema_version": prompt.schema_version,
        "prompt_version": new_prompt_version,
        "created_at": datetime.now(UTC),
        "created_by": current_user.user_id,
        "tag_ids": prompt.tag_ids,
        "model": prompt.model,
        "organization_id": organization_id
    }
    
    # Insert into MongoDB
    result = await db.prompt_revisions.insert_one(prompt_dict)
    
    # Return complete prompt, adding name from prompts collection
    prompt_dict["name"] = prompt.name
    prompt_dict["prompt_revid"] = str(result.inserted_id)
    return Prompt(**prompt_dict)

@app.get("/v0/orgs/{organization_id}/prompts", response_model=ListPromptsResponse, tags=["prompts"])
async def list_prompts(
    organization_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    document_id: str = Query(None, description="Filter prompts by document's tags"),
    tag_ids: str = Query(None, description="Comma-separated list of tag IDs"),
    name_search: str = Query(None, description="Search term for prompt names"),
    current_user: User = Depends(get_org_user)
):
    """List prompts within an organization"""
    logger.info(f"list_prompts() start: organization_id: {organization_id}, skip: {skip}, limit: {limit}, document_id: {document_id}, tag_ids: {tag_ids}")
    db = ad.common.get_async_db()
    
    # First, get prompts that belong to the organization
    # Base query for prompts in org, with optional name search
    prompts_query = {"organization_id": organization_id}
    if name_search:
        prompts_query["name"] = {"$regex": name_search, "$options": "i"}

    org_prompts = await db.prompts.find(prompts_query).to_list(None)
    
    if not org_prompts:
        return ListPromptsResponse(prompts=[], total_count=0, skip=skip)
    
    # Extract prompt IDs
    prompt_ids = [prompt["_id"] for prompt in org_prompts]
    prompt_id_to_name = {str(prompt["_id"]): prompt["name"] for prompt in org_prompts}
    
    # Build pipeline for prompt_revisions
    pipeline = [
        {
            "$match": {"prompt_id": {"$in": [str(pid) for pid in prompt_ids]}}
        }
    ]

    pipeline.extend([
        {
            "$sort": {"_id": -1}
        },
        {
            "$group": {
                "_id": "$prompt_id",
                "doc": {"$first": "$$ROOT"}
            }
        },
        {
            "$replaceRoot": {"newRoot": "$doc"}
        },
        {
            "$sort": {"_id": -1}
        }
    ])

    # Add document tag filtering if document_id is provided
    if document_id:
        document = await db.docs.find_one({
            "_id": ObjectId(document_id),
            "organization_id": organization_id  # Ensure document belongs to org
        })
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        document_tag_ids = document.get("tag_ids", [])
        if document_tag_ids:
            pipeline.append({
                "$match": {"tag_ids": {"$in": document_tag_ids}}
            })
        else:
            # Only return the default prompt if no tags
            pipeline.append({
                "$match": {"name": "Default Prompt"}
            })
    # Add direct tag filtering if tag_ids are provided
    if tag_ids:
        document_tag_ids = [tid.strip() for tid in tag_ids.split(",")]
        pipeline.append({
            "$match": {"tag_ids": {"$all": document_tag_ids}}
        })

    pipeline.extend([
        {
            "$facet": {
                "total": [{"$count": "count"}],
                "prompts": [
                    {"$skip": skip},
                    {"$limit": limit}
                ]
            }
        }
    ])
    
    result = await db.prompt_revisions.aggregate(pipeline).to_list(length=1)
    result = result[0]
    
    total_count = result["total"][0]["count"] if result["total"] else 0
    prompts = result["prompts"]

    # Convert _id to id in each prompt and add name from prompts collection
    for prompt in prompts:
        prompt['prompt_revid'] = str(prompt.pop('_id'))
        prompt['name'] = prompt_id_to_name.get(prompt['prompt_id'], "Unknown")
    
    ret = ListPromptsResponse(
        prompts=prompts,
        total_count=total_count,
        skip=skip
    )
    return ret

@app.get("/v0/orgs/{organization_id}/prompts/{prompt_revid}", response_model=Prompt, tags=["prompts"])
async def get_prompt(
    organization_id: str,
    prompt_revid: str,
    current_user: User = Depends(get_org_user)
):
    """Get a prompt"""
    logger.info(f"get_prompt() start: organization_id: {organization_id}, prompt_revid: {prompt_revid}")
    db = ad.common.get_async_db()
    
    # Get the prompt revision
    revision = await db.prompt_revisions.find_one({
        "_id": ObjectId(prompt_revid)
    })
    if not revision:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    # Get the prompt name and verify organization
    prompt = await db.prompts.find_one({
        "_id": ObjectId(revision["prompt_id"]),
        "organization_id": organization_id
    })
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found or not in this organization")
    
    # Combine the data
    revision['prompt_revid'] = str(revision.pop('_id'))
    revision['name'] = prompt['name']
    
    return Prompt(**revision)

@app.put("/v0/orgs/{organization_id}/prompts/{prompt_id}", response_model=Prompt, tags=["prompts"])
async def update_prompt(
    organization_id: str,
    prompt_id: str,
    prompt: PromptConfig,
    current_user: User = Depends(get_org_user)
):
    """Update a prompt"""
    logger.info(f"update_prompt() start: organization_id: {organization_id}, prompt_id: {prompt_id}")
    db = ad.common.get_async_db()

    # Check if the prompt exists
    existing_prompt = await db.prompts.find_one({
        "_id": ObjectId(prompt_id),
        "organization_id": organization_id
    })
    if not existing_prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    # Only verify schema if one is specified
    schema = await validate_and_resolve_schema(prompt)

    # Validate model exists
    found = False
    for provider in await db.llm_providers.find({}).to_list(None):
        if prompt.model in provider["litellm_models_enabled"]:
            found = True
            break
    if not found:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model: {prompt.model}"
        )
    
    # Validate tag IDs if provided
    if prompt.tag_ids:
        tags_cursor = db.tags.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in prompt.tag_ids]},
            "organization_id": organization_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = set(prompt.tag_ids) - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            ) 

    # Get the latest prompt revision
    latest_prompt_revision = await db.prompt_revisions.find_one(
        {"prompt_id": prompt_id},
        sort=[("prompt_version", -1)]
    )
    if not latest_prompt_revision:
        raise HTTPException(status_code=404, detail="Prompt not found or not in this organization")

    # Check if only the name has changed
    only_name_changed = (
        prompt.name != existing_prompt["name"] and
        prompt.content == latest_prompt_revision["content"] and
        prompt.schema_id == latest_prompt_revision.get("schema_id") and
        prompt.schema_version == latest_prompt_revision.get("schema_version") and
        prompt.model == latest_prompt_revision["model"] and
        set(prompt.tag_ids or []) == set(latest_prompt_revision.get("tag_ids") or [])
    )
    
    if prompt.name != existing_prompt["name"]:
        # Update the name in the prompts collection
        result = await db.prompts.update_one(
            {"_id": ObjectId(prompt_id)},
            {"$set": {"name": prompt.name}}
        )

        if only_name_changed:
            if result.modified_count > 0:
                # Return the updated prompt
                updated_revision = latest_prompt_revision.copy()
                updated_revision["prompt_revid"] = str(updated_revision.pop("_id"))
                updated_revision["name"] = prompt.name
                return Prompt(**updated_revision)
            else:
                raise HTTPException(status_code=500, detail="Failed to update prompt name")
    
    # If other fields changed, create a new version
    # Get the next version number using the stable prompt_id
    _, new_prompt_version = await get_prompt_id_and_version(prompt_id)
    
    # Create new version of the prompt in prompt_revisions
    new_prompt = {
        "prompt_id": prompt_id,  # Preserve stable identifier
        "content": prompt.content,
        "schema_id": prompt.schema_id,
        "schema_version": prompt.schema_version,
        "prompt_version": new_prompt_version,
        "created_at": datetime.now(UTC),
        "created_by": current_user.user_id,
        "tag_ids": prompt.tag_ids,
        "model": prompt.model
    }
    
    # Insert new version
    result = await db.prompt_revisions.insert_one(new_prompt)
    
    # Return updated prompt
    new_prompt["prompt_revid"] = str(result.inserted_id)
    new_prompt["name"] = prompt.name  # Add name from the prompts collection
    return Prompt(**new_prompt)

@app.delete("/v0/orgs/{organization_id}/prompts/{prompt_id}", tags=["prompts"])
async def delete_prompt(
    organization_id: str,
    prompt_id: str,
    current_user: User = Depends(get_org_user)
):
    """Delete a prompt"""
    logger.info(f"delete_prompt() start: organization_id: {organization_id}, prompt_id: {prompt_id}")
    db = ad.common.get_async_db()
    
    # Get the prompt revision   
    prompt_revision = await db.prompt_revisions.find_one({
        "prompt_id": prompt_id
    })
    if not prompt_revision:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    # Get the prompt and verify organization
    prompt = await db.prompts.find_one({
        "_id": ObjectId(prompt_id),
        "organization_id": organization_id
    })
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found or not in this organization")
  
    # Delete all revisions of this prompt
    result = await db.prompt_revisions.delete_many({
        "prompt_id": prompt_id
    })
    
    # Delete the prompt entry
    await db.prompts.delete_one({"_id": ObjectId(prompt_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No prompt revisions found")
        
    return {"message": "Prompt deleted successfully"}

# Add this helper function near the top of the file with other functions
async def get_form_id_and_version(form_id: Optional[str] = None) -> tuple[str, int]:
    """
    Get the next version for an existing form or create a new form identifier.
    
    Args:
        form_id: Existing form ID, or None to create a new one
        
    Returns:
        Tuple of (form_id, form_version)
    """
    db = ad.common.get_async_db()

    if form_id is None:
        # Insert a placeholder document to get MongoDB-generated ID
        result = await db.forms.insert_one({
            "form_version": 1
        })
        
        # Use the MongoDB-assigned _id as our form_id
        form_id = str(result.inserted_id)
        form_version = 1
    else:
        # Get the next version for an existing form
        result = await db.forms.find_one_and_update(
            {"_id": ObjectId(form_id)},
            {"$inc": {"form_version": 1}},
            upsert=True,
            return_document=True
        )
        form_version = result["form_version"]
    
    return form_id, form_version

# Form management endpoints
@app.post("/v0/orgs/{organization_id}/forms", response_model=Form, tags=["forms"])
async def create_form(
    organization_id: str,
    form: FormConfig,
    current_user: User = Depends(get_org_user)
):
    """Create a form"""
    logger.info(f"create_form() start: organization_id: {organization_id}, form: {form}")
    db = ad.common.get_async_db()

    # Check if form with this name already exists (case-insensitive)
    existing_form = await db.forms.find_one({
        "name": {"$regex": f"^{form.name}$", "$options": "i"},
        "organization_id": organization_id
    })

    # Generate form_id and version
    if existing_form:
        form_id, new_form_version = await get_form_id_and_version(str(existing_form["_id"]))
    else:
        # Generate a new form_id when creating a new form
        form_id, new_form_version = await get_form_id_and_version(None)
    
    # Update the forms collection with name and organization_id
    await db.forms.update_one(
        {"_id": ObjectId(form_id)},
        {"$set": {
            "name": form.name,
            "organization_id": organization_id,
        }},
        upsert=True
    )
    
    # Create form document for form_revisions
    form_dict = {
        "form_id": form_id,
        "response_format": form.response_format.model_dump(),
        "form_version": new_form_version,
        "tag_ids": form.tag_ids,
        "created_at": datetime.now(UTC),
        "created_by": current_user.user_id
    }
    
    # Insert into MongoDB
    result = await db.form_revisions.insert_one(form_dict)
    
    # Return complete form
    form_dict["name"] = form.name
    form_dict["form_revid"] = str(result.inserted_id)
    return Form(**form_dict)

@app.get("/v0/orgs/{organization_id}/forms", response_model=ListFormsResponse, tags=["forms"])
async def list_forms(
    organization_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    tag_ids: str = Query(None, description="Comma-separated list of tag IDs to filter by"),
    current_user: User = Depends(get_org_user)
):
    """List latest form revisions within an organization, optionally filtered by tags"""
    logger.info(f"list_forms() start: organization_id: {organization_id}, skip: {skip}, limit: {limit}, tag_ids: {tag_ids}")
    db = ad.common.get_async_db()
    
    # Parse tag IDs if provided
    filter_tag_ids = []
    if tag_ids:
        filter_tag_ids = [tag_id.strip() for tag_id in tag_ids.split(',') if tag_id.strip()]
    
    # Get all forms that belong to the organization
    org_forms = await db.forms.find({"organization_id": organization_id}).to_list(None)
    
    if not org_forms:
        return ListFormsResponse(forms=[], total_count=0, skip=skip)

    logger.info(f"list_forms() org_forms: {org_forms}")
    
    # Extract form IDs
    form_ids = [form["_id"] for form in org_forms]
    form_id_to_name = {str(form["_id"]): form["name"] for form in org_forms}
    
    # Build pipeline for form_revisions with tag filtering
    pipeline = [
        {
            "$match": {"form_id": {"$in": [str(fid) for fid in form_ids]}}
        }
    ]
    
    # Add tag filtering if tag_ids are provided
    if filter_tag_ids:
        pipeline.append({
            "$match": {"tag_ids": {"$in": filter_tag_ids}}
        })
    
    # Continue with the rest of the pipeline
    pipeline.extend([
        {
            "$sort": {"_id": -1}
        },
        {
            "$group": {
                "_id": "$form_id",
                "doc": {"$first": "$$ROOT"}
            }
        },
        {
            "$replaceRoot": {"newRoot": "$doc"}
        },
        {
            "$sort": {"_id": -1}
        },
        {
            "$facet": {
                "total": [{"$count": "count"}],
                "forms": [
                    {"$skip": skip},
                    {"$limit": limit}
                ]
            }
        }
    ])
    
    result = await db.form_revisions.aggregate(pipeline).to_list(length=1)
    result = result[0]
    
    total_count = result["total"][0]["count"] if result["total"] else 0
    forms = result["forms"]
    
    # Convert _id to id in each form and add name from forms collection
    for form in forms:
        form['form_revid'] = str(form.pop('_id'))
        form['name'] = form_id_to_name.get(form['form_id'], "Unknown")
    
    return ListFormsResponse(
        forms=forms,
        total_count=total_count,
        skip=skip
    )

# Form submission endpoints
@app.post("/v0/orgs/{organization_id}/forms/submissions/{document_id}", response_model=FormSubmission, tags=["form-submissions"])
async def submit_form(
    organization_id: str,
    document_id: str,
    submission: FormSubmissionData,
    current_user: User = Depends(get_org_user)
):
    """Submit a form for a specific document - updates existing submission if found"""
    logger.info(f"submit_form() start: organization_id: {organization_id}, document_id: {document_id}, form_revid: {submission.form_revid}")
    db = ad.common.get_async_db()
    
    # Verify the form revision exists
    form_revision = await db.form_revisions.find_one({"_id": ObjectId(submission.form_revid)})
    if not form_revision:
        raise HTTPException(status_code=404, detail="Form revision not found")
    
    # Verify the form belongs to the organization
    form = await db.forms.find_one({
        "_id": ObjectId(form_revision["form_id"]),
        "organization_id": organization_id
    })
    if not form:
        raise HTTPException(status_code=404, detail="Form not found or not in this organization")

    # Verify the document exists and belongs to the organization
    document = await db.docs.find_one({
        "_id": ObjectId(document_id),
        "organization_id": organization_id
    })
    if not document:
        raise HTTPException(status_code=404, detail="Document not found or not in this organization")
    
    # Check if a submission already exists for this form/document combination
    existing_submission = await db.form_submissions.find_one({
        "form_revid": submission.form_revid,
        "document_id": document_id,
        "organization_id": organization_id
    })
    
    now = datetime.now(UTC)
    
    if existing_submission:
        # Update existing submission
        update_data = {
            "submission_data": submission.submission_data,
            "submitted_by": submission.submitted_by or current_user.user_id,
            "updated_at": now
        }
        
        await db.form_submissions.update_one(
            {"_id": existing_submission["_id"]},
            {"$set": update_data}
        )
        
        # Return updated submission
        updated_submission = await db.form_submissions.find_one({"_id": existing_submission["_id"]})
        updated_submission["id"] = str(updated_submission.pop('_id'))
        return FormSubmission(**updated_submission)
    else:
        # Create new submission
        submission_doc = {
            "form_revid": submission.form_revid,
            "document_id": document_id,
            "organization_id": organization_id,
            "submission_data": submission.submission_data,
            "submitted_by": submission.submitted_by or current_user.user_id,
            "created_at": now,
            "updated_at": now
        }
        
        result = await db.form_submissions.insert_one(submission_doc)
        
        # Return the created submission
        submission_doc["id"] = str(result.inserted_id)
        return FormSubmission(**submission_doc)


@app.get("/v0/orgs/{organization_id}/forms/submissions/{document_id}", response_model=Optional[FormSubmission], tags=["form-submissions"])
async def get_form_submission(
    organization_id: str,
    document_id: str,
    form_revid: str = Query(..., description="The form revision ID"),
    current_user: User = Depends(get_org_user)
):
    """Get the form submission for a specific document and form combination"""
    logger.info(f"get_form_submission() start: organization_id: {organization_id}, document_id: {document_id}, form_revid: {form_revid}")
    db = ad.common.get_async_db()
    
    submission = await db.form_submissions.find_one({
        "document_id": document_id,
        "form_revid": form_revid,
        "organization_id": organization_id
    })
    
    if submission:
        submission["id"] = str(submission.pop('_id'))
        return FormSubmission(**submission)
    
    return None

@app.delete("/v0/orgs/{organization_id}/forms/submissions/{document_id}", tags=["form-submissions"])
async def delete_form_submission(
    organization_id: str,
    document_id: str,
    form_revid: str = Query(..., description="The form revision ID"),
    current_user: User = Depends(get_org_user)
):
    """Delete a form submission"""
    logger.info(f"delete_form_submission() start: organization_id: {organization_id}, document_id: {document_id}, form_revid: {form_revid}")
    db = ad.common.get_async_db()
    
    result = await db.form_submissions.delete_one({
        "document_id": document_id,
        "form_revid": form_revid,
        "organization_id": organization_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Form submission not found")
    
    return {"message": "Form submission deleted successfully"}

@app.get("/v0/orgs/{organization_id}/forms/{form_revid}", response_model=Form, tags=["forms"])
async def get_form(
    organization_id: str,
    form_revid: str,
    current_user: User = Depends(get_org_user)
):
    """Get a form revision"""
    logger.info(f"get_form() start: organization_id: {organization_id}, form_revid: {form_revid}")
    db = ad.common.get_async_db()
    
    # Get the form revision
    revision = await db.form_revisions.find_one({"_id": ObjectId(form_revid)})
    if not revision:
        raise HTTPException(status_code=404, detail="Form revision not found")
    
    # Get the form name and verify organization
    form = await db.forms.find_one({
        "_id": ObjectId(revision["form_id"]),
        "organization_id": organization_id
    })
    if not form:
        raise HTTPException(status_code=404, detail="Form not found or not in this organization")
    
    # Transform the data to match Form model
    form_data = {
        "name": form["name"],
        "response_format": revision["response_format"],
        "form_revid": str(revision['_id']),
        "form_id": revision["form_id"],
        "form_version": revision["form_version"],
        "tag_ids": revision["tag_ids"],
        "created_at": revision["created_at"],
        "created_by": revision["created_by"],
    }
    
    return Form(**form_data)

@app.put("/v0/orgs/{organization_id}/forms/{form_id}", response_model=Form, tags=["forms"])
async def update_form(
    organization_id: str,
    form_id: str,
    form: FormConfig,
    current_user: User = Depends(get_org_user)
):
    """Update an existing form"""
    logger.info(f"update_form() start: organization_id: {organization_id}, form_id: {form_id}")
    db = ad.common.get_async_db()

    # Get the existing form and latest revision
    existing_form = await db.forms.find_one({"_id": ObjectId(form_id), "organization_id": organization_id})
    if not existing_form:
        raise HTTPException(status_code=404, detail="Form not found")
    
    latest_revision = await db.form_revisions.find_one(
        {"form_id": form_id},
        sort=[("form_version", -1)]
    )

    logger.info(f"update_form() latest_revision: {latest_revision}")
    
    if not latest_revision:
        raise HTTPException(status_code=404, detail="Form not found")
    
    # Check if response_format or tag_ids has actually changed
    current_response_format = latest_revision.get("response_format", {})
    new_response_format = form.response_format.model_dump()
    
    response_format_changed = current_response_format != new_response_format
    tag_ids_changed = set(latest_revision.get("tag_ids", [])) != set(form.tag_ids)

    logger.info(f"update_form() response_format_changed: {response_format_changed}")
    logger.info(f"update_form() name: {form.name}")
    logger.info(f"update_form() tag_ids: {form.tag_ids}")
    
    # Update the form metadata in the forms collection
    await db.forms.update_one(
        {"_id": ObjectId(form_id)},
        {"$set": {
            "name": form.name,
        }},
        upsert=True
    )
    
    # Only create a new revision if the response_format has changed
    if response_format_changed or tag_ids_changed:
        new_form_version = latest_revision["form_version"] + 1
        
        # Create new form revision
        new_revision = {
            "form_id": form_id,
            "response_format": new_response_format,
            "tag_ids": form.tag_ids,
            "form_version": new_form_version,
            "created_at": datetime.now(UTC),
            "created_by": current_user.user_id
        }
        
        # Insert new revision
        result = await db.form_revisions.insert_one(new_revision)

        logger.info(f"update_form() new revision: {new_revision}")
        
        # Return updated form with new revision
        return Form(
            form_revid=str(result.inserted_id),
            form_id=new_revision["form_id"],
            form_version=new_revision["form_version"],
            name=form.name,
            response_format=new_revision["response_format"],
            tag_ids=new_revision["tag_ids"],
            created_at=new_revision["created_at"],
            created_by=new_revision["created_by"],
        )
    else:
        logger.info(f"update_form() no revision change")

        # No revision change, just return the existing form with updated metadata
        return Form(
            form_revid=str(latest_revision["_id"]),
            form_id=form_id,
            name=form.name,
            response_format=form.response_format,
            form_version=latest_revision["form_version"],
            tag_ids=latest_revision["tag_ids"],
            created_at=latest_revision["created_at"],
            created_by=latest_revision["created_by"],
        )

@app.delete("/v0/orgs/{organization_id}/forms/{form_id}", tags=["forms"])
async def delete_form(
    organization_id: str,
    form_id: str,
    current_user: User = Depends(get_org_user)
):
    """Delete a form"""
    logger.info(f"delete_form() start: organization_id: {organization_id}, form_id: {form_id}")

    db = ad.common.get_async_db()
    
    # Get the form and verify organization
    form = await db.forms.find_one({
        "_id": ObjectId(form_id),
        "organization_id": organization_id
    })
    if not form:
        raise HTTPException(status_code=404, detail="Form not found or not in this organization")

    # Check for dependent prompts by form_id
    dependent_prompts = await db.prompt_revisions.find({
        "form_id": form_id
    }).to_list(None)
    
    if dependent_prompts:
        # Get names from prompts collection
        prompt_names = {}
        for prompt_revision in dependent_prompts:
            prompt = await db.prompts.find_one({"_id": ObjectId(prompt_revision["prompt_id"])})
            if prompt:
                prompt_names[str(prompt_revision["_id"])] = prompt["name"]
        
        # Format the list of dependent prompts
        prompt_list = [
            {
                "name": prompt_names.get(str(p["_id"]), "Unknown"), 
                "form_version": p["form_version"]
            } 
            for p in dependent_prompts
        ]
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete form because it has dependent prompts:{json.dumps(prompt_list)}"
        )
    
    # If no dependent prompts, proceed with deletion
    result = await db.form_revisions.delete_many({
        "form_id": form_id
    })
    
    # Delete the form entry
    await db.forms.delete_one({"_id": ObjectId(form_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No form revisions found")
        
    return {"message": "Form deleted successfully"}

# Tag management endpoints
@app.post("/v0/orgs/{organization_id}/tags", response_model=Tag, tags=["tags"])
async def create_tag(
    organization_id: str,
    tag: TagConfig,
    current_user: User = Depends(get_org_user)
):
    """Create a tag"""
    db = ad.common.get_async_db()
    # Check if tag with this name already exists for this user
    existing_tag = await db.tags.find_one({
        "name": tag.name,
        "organization_id": organization_id
    })
    
    if existing_tag:
        raise HTTPException(
            status_code=400,
            detail=f"Tag with name '{tag.name}' already exists"
        )

    new_tag = {
        "name": tag.name,
        "color": tag.color,
        "description": tag.description,
        "created_at": datetime.now(UTC),
        "created_by": current_user.user_id,
        "organization_id": organization_id  # Add organization_id
    }
    
    result = await db.tags.insert_one(new_tag)
    new_tag["id"] = str(result.inserted_id)
    
    return Tag(**new_tag)

@app.get("/v0/orgs/{organization_id}/tags", response_model=ListTagsResponse, tags=["tags"])
async def list_tags(
    organization_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    name_search: str = Query(None, description="Search term for tag names"),
    current_user: User = Depends(get_org_user)
):
    """List tags"""
    db = ad.common.get_async_db()
    # Get total count
    total_count = await db.tags.count_documents({"organization_id": organization_id})
    
    # Build base query with optional name search
    tags_query = {"organization_id": organization_id}
    if name_search:
        tags_query["name"] = {"$regex": name_search, "$options": "i"}

    # Get paginated tags with sorting by _id in descending order
    cursor = db.tags.find(tags_query).sort("_id", -1).skip(skip).limit(limit)
    
    tags = await cursor.to_list(length=None)
    
    return ListTagsResponse(
        tags=[
            {
                "id": str(tag["_id"]),
                "name": tag["name"],
                "color": tag.get("color"),
                "description": tag.get("description"),
                "created_at": tag["created_at"],
                "created_by": tag["created_by"]
            }
            for tag in tags
        ],
        total_count=total_count,
        skip=skip
    )

@app.delete("/v0/orgs/{organization_id}/tags/{tag_id}", tags=["tags"])
async def delete_tag(
    organization_id: str,
    tag_id: str,
    current_user: User = Depends(get_org_user)
):
    """Delete a tag"""
    db = ad.common.get_async_db()
    # Verify tag exists and belongs to user
    tag = await db.tags.find_one({
        "_id": ObjectId(tag_id),
        "organization_id": organization_id
    })
    
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Check if tag is used in any documents
    documents_with_tag = await db.docs.find_one({
        "tag_ids": tag_id
    })
    
    if documents_with_tag:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete tag '{tag['name']}' because it is assigned to one or more documents"
        )

    # Check if tag is used in any prompts
    prompts_with_tag = await db.prompt_revisions.find_one({
        "tag_ids": tag_id
    })
    
    if prompts_with_tag:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete tag '{tag['name']}' because it is assigned to one or more prompts"
        )
    
    result = await db.tags.delete_one({
        "_id": ObjectId(tag_id),
        "organization_id": organization_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    return {"message": "Tag deleted successfully"}

@app.put("/v0/orgs/{organization_id}/tags/{tag_id}", response_model=Tag, tags=["tags"])
async def update_tag(
    organization_id: str,
    tag_id: str,
    tag: TagConfig,
    current_user: User = Depends(get_org_user)
):
    """Update a tag"""
    db = ad.common.get_async_db()
    
    # Verify tag exists and belongs to user
    existing_tag = await db.tags.find_one({
        "_id": ObjectId(tag_id),
        "organization_id": organization_id
    })
    
    if not existing_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Update all fields including name
    update_data = {
        "name": tag.name,
        "color": tag.color,
        "description": tag.description
    }
    
    updated_tag = await db.tags.find_one_and_update(
        {"_id": ObjectId(tag_id)},
        {"$set": update_data},
        return_document=True
    )
    
    if not updated_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    return Tag(**{**updated_tag, "id": str(updated_tag["_id"])})

@app.get("/v0/orgs/{organization_id}/tags/{tag_id}", response_model=Tag, tags=["tags"])
async def get_tag(
    organization_id: str,
    tag_id: str,
    current_user: User = Depends(get_org_user)
):
    """Get a tag by ID"""
    db = ad.common.get_async_db()
    tag = await db.tags.find_one({
        "_id": ObjectId(tag_id),
        "organization_id": organization_id
    })
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    return Tag(**{**tag, "id": str(tag["_id"])})

async def get_flow_id_and_version(flow_id: Optional[str] = None) -> tuple[str, int]:
    """Get flow ID and version for creating or updating flows"""
    db = ad.common.get_async_db()
    
    if flow_id:
        # Get existing flow to determine version
        existing_flow = await db.flows.find_one({"flow_id": flow_id})
        if existing_flow:
            return flow_id, existing_flow["flow_version"] + 1
        else:
            return flow_id, 1
    else:
        # Generate new flow ID
        flow_id = str(ObjectId())
        return flow_id, 1

@app.post("/v0/orgs/{organization_id}/flows", tags=["flows"])
async def create_flow(
    organization_id: str,
    flow: FlowConfig,
    current_user: User = Depends(get_org_user)
) -> Flow:
    # Check if user has access to organization
    is_org_member = await is_organization_member(organization_id, current_user.user_id)
    if not is_org_member:
        raise HTTPException(status_code=403, detail="Access denied to organization")

    db = ad.common.get_async_db()
    
    # Get flow_id and version
    flow_id, flow_version = await get_flow_id_and_version()
    
    # Create flow document
    flow_doc = {
        "flow_id": flow_id,
        "flow_version": flow_version,
        "organization_id": organization_id,
        "created_by": current_user.user_id,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        **flow.model_dump()
    }
    
    result = await db.flows.insert_one(flow_doc)
    
    # Return the created flow
    return Flow(
        flow_revid=str(result.inserted_id),
        flow_id=flow_id,
        flow_version=flow_version,
        organization_id=organization_id,
        created_by=current_user.user_id,
        created_at=flow_doc["created_at"],
        updated_at=flow_doc["updated_at"],
        **flow.model_dump()
    )

@app.get("/v0/orgs/{organization_id}/flows", tags=["flows"])
async def list_flows(
    organization_id: str,
    skip: int = 0,
    limit: int = 10,
    current_user: User = Depends(get_org_user)
) -> ListFlowsResponse:
    # Check if user has access to organization
    is_org_member = await is_organization_member(organization_id, current_user.user_id)
    if not is_org_member:
        raise HTTPException(status_code=403, detail="Access denied to organization")
    
    db = ad.common.get_async_db()
    
    # Get total count for pagination
    total_count = await db.flows.count_documents({"organization_id": organization_id})
    
    # Get flows for the organization
    flows_cursor = db.flows.find(
        {"organization_id": organization_id}
    ).sort("created_at", -1).skip(skip).limit(limit)
    
    # Convert cursor to list
    flows = []
    
    async for flow_doc in flows_cursor:
        flow = Flow(
            flow_revid=str(flow_doc["_id"]),
            flow_id=flow_doc["flow_id"],
            flow_version=flow_doc["flow_version"],
            organization_id=flow_doc["organization_id"],
            created_at=flow_doc["created_at"],
            created_by=flow_doc["created_by"],
            updated_at=flow_doc["updated_at"],
            name=flow_doc["name"],
            description=flow_doc.get("description"),
            nodes=flow_doc["nodes"],
            edges=flow_doc["edges"],
            tag_ids=flow_doc.get("tag_ids")
        )
        flows.append(flow)
    
    return ListFlowsResponse(
        flows=flows,
        total_count=total_count,
        skip=skip
    )

@app.get("/v0/orgs/{organization_id}/flows/{flow_id}", tags=["flows"])
async def get_flow(
    organization_id: str,
    flow_id: str,
    current_user: User = Depends(get_org_user)
) -> Flow:
    # Check if user has access to organization
    is_org_member = await is_organization_member(organization_id, current_user.user_id)
    if not is_org_member:
        raise HTTPException(status_code=403, detail="Access denied to organization")
    
    db = ad.common.get_async_db()

    # Get the latest version of the flow
    flow_doc = await db.flows.find_one(
        {"flow_id": flow_id, "organization_id": organization_id},
        sort=[("flow_version", -1)]
    )
    
    if not flow_doc:
        raise HTTPException(status_code=404, detail="Flow not found")
    
    return Flow(**{**flow_doc, "flow_revid": str(flow_doc["_id"])})

@app.delete("/v0/orgs/{organization_id}/flows/{flow_id}", tags=["flows"])
async def delete_flow(
    organization_id: str,
    flow_id: str,
    current_user: User = Depends(get_org_user)
) -> dict:
    # Check if user has access to organization
    is_org_member = await is_organization_member(organization_id, current_user.user_id)
    if not is_org_member:
        raise HTTPException(status_code=403, detail="Access denied to organization")
    
    db = ad.common.get_async_db()

    # Delete all versions of the flow
    result = await db.flows.delete_many({
        "flow_id": flow_id,
        "organization_id": organization_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Flow not found")
    
    return {"message": f"Deleted {result.deleted_count} flow versions"}

@app.put("/v0/orgs/{organization_id}/flows/{flow_id}", tags=["flows"])
async def update_flow(
    organization_id: str,
    flow_id: str,
    flow: FlowConfig,
    current_user: User = Depends(get_current_user)
) -> Flow:
    # Check if user has access to organization
    is_org_member = await is_organization_member(organization_id, current_user.user_id)
    if not is_org_member:
        raise HTTPException(status_code=403, detail="Access denied to organization")
    
    db = ad.common.get_async_db()

    # Get next version number
    _, flow_version = await get_flow_id_and_version(flow_id)
    
    # Create updated flow document
    flow_doc = {
        "flow_id": flow_id,
        "flow_version": flow_version,
        "organization_id": organization_id,
        "name": flow.name,
        "description": flow.description,
        "nodes": flow.nodes,
        "edges": flow.edges,
        "tag_ids": flow.tag_ids or [],
        "created_at": datetime.utcnow(),
        "created_by": current_user.user_id,
        "updated_at": datetime.utcnow()
    }
    
    # Insert new version
    result = await db.flows.insert_one(flow_doc)
    
    return Flow(**{**flow_doc, "flow_revid": str(result.inserted_id)})

@app.post("/v0/account/auth/token", tags=["account/auth"])
async def create_auth_token(request: Request):
    """
    Create an authentication token.
    Requires HMAC signature in X-Request-Signature header for authentication.
    """
    # Verify request signature
    signature = request.headers.get("X-Request-Signature")
    if not signature:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Request-Signature header"
        )

    if not NEXTAUTH_SECRET:
        logger.error("NEXTAUTH_SECRET not configured")
        raise HTTPException(
            status_code=500,
            detail="Server configuration error"
        )

    # Get raw request body for signature verification
    raw_body = await request.body()

    # Verify signature using HMAC-SHA256 on the raw body
    expected_signature = hmac.new(
        NEXTAUTH_SECRET.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        logger.warning(f"Invalid signature for token request")
        raise HTTPException(
            status_code=401,
            detail="Invalid request signature"
        )

    # Parse request body after signature verification
    try:
        user_data = json.loads(raw_body)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON: {str(e)}"
        )

    logger.debug(f"create_auth_token(): user_data: {user_data}")

    # Signature is valid, create token
    token = jwt.encode(
        {
            "userId": user_data["id"],
            "userName": user_data["name"],
            "email": user_data["email"]
        },
        NEXTAUTH_SECRET,
        algorithm=ALGORITHM
    )

    logger.debug(f"create_auth_token(): {token}")
    return {"token": token}

@app.post("/v0/account/auth/oauth", response_model=OAuthSignInResponse, tags=["account/auth"])
async def oauth_signin(request: Request):
    """
    Handle OAuth sign-in from NextAuth.
    Creates/links user, account, organization, and syncs with Stripe.
    Requires HMAC signature in X-Request-Signature header for authentication.
    """
    # Verify request signature
    signature = request.headers.get("X-Request-Signature")
    if not signature:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Request-Signature header"
        )

    if not NEXTAUTH_SECRET:
        logger.error("NEXTAUTH_SECRET not configured")
        raise HTTPException(
            status_code=500,
            detail="Server configuration error"
        )

    # Get raw request body for signature verification
    raw_body = await request.body()

    # Verify signature using HMAC-SHA256 on the raw body
    expected_signature = hmac.new(
        NEXTAUTH_SECRET.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        logger.warning(f"Invalid signature for OAuth request")
        raise HTTPException(
            status_code=401,
            detail="Invalid request signature"
        )

    # Parse and validate the request body after signature verification
    try:
        oauth_data = json.loads(raw_body)
        oauth_request = OAuthSignInRequest(**oauth_data)
    except (json.JSONDecodeError, Exception) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid request body: {str(e)}"
        )

    logger.info(f"oauth_signin(): email={oauth_request.email}, provider={oauth_request.provider}")

    # Process OAuth sign-in
    db = ad.common.get_async_db()

    try:
        # Check if user already exists
        existing_user = await db.users.find_one({"email": oauth_request.email})

        if existing_user:
            user_id = str(existing_user["_id"])
            is_new_user = False
            logger.info(f"Existing user found: {user_id}")

            # Check if this OAuth account already exists
            existing_account = await db.accounts.find_one({
                "provider": oauth_request.provider,
                "providerAccountId": oauth_request.provider_account_id
            })

            if not existing_account:
                # Link OAuth account to existing user
                await db.accounts.insert_one({
                    "userId": user_id,
                    "type": oauth_request.account.type,
                    "provider": oauth_request.provider,
                    "providerAccountId": oauth_request.provider_account_id,
                    "access_token": oauth_request.account.access_token,
                    "expires_at": oauth_request.account.expires_at,
                    "token_type": oauth_request.account.token_type,
                    "scope": oauth_request.account.scope,
                    "id_token": oauth_request.account.id_token,
                    "refresh_token": oauth_request.account.refresh_token
                })
                logger.info(f"Linked {oauth_request.provider} account to existing user {user_id}")
        else:
            # Create new user
            is_new_user = True
            user_result = await db.users.insert_one({
                "email": oauth_request.email,
                "name": oauth_request.name,
                "role": "user",
                "email_verified": oauth_request.email_verified,
                "created_at": datetime.now(UTC)
            })
            user_id = str(user_result.inserted_id)
            logger.info(f"Created new user: {user_id}")

            # Create OAuth account record
            await db.accounts.insert_one({
                "userId": user_id,
                "type": oauth_request.account.type,
                "provider": oauth_request.provider,
                "providerAccountId": oauth_request.provider_account_id,
                "access_token": oauth_request.account.access_token,
                "expires_at": oauth_request.account.expires_at,
                "token_type": oauth_request.account.token_type,
                "scope": oauth_request.account.scope,
                "id_token": oauth_request.account.id_token,
                "refresh_token": oauth_request.account.refresh_token
            })
            logger.info(f"Created {oauth_request.provider} account for user {user_id}")

            # Create individual organization using email as name
            org_result = await db.organizations.insert_one({
                "name": oauth_request.email,
                "type": "individual",
                "members": [{
                    "user_id": user_id,
                    "role": "admin"
                }],
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC)
            })
            org_id = str(org_result.inserted_id)
            logger.info(f"Created organization {org_id} for user {user_id}")

            # Sync with Stripe
            try:
                await sync_customer(db=db, org_id=org_id)
                logger.info(f"Successfully synced organization {org_id} with Stripe")
            except Exception as e:
                logger.warning(f"Failed to sync organization {org_id} with Stripe: {e}")
                # Don't fail the sign-in if Stripe sync fails

        return OAuthSignInResponse(
            success=True,
            user_id=user_id,
            is_new_user=is_new_user
        )

    except Exception as e:
        logger.error(f"Error in oauth_signin: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process OAuth sign-in: {str(e)}"
        )

@app.get("/v0/account/llm/models", response_model=ListLLMModelsResponse, tags=["account/llm"])
async def list_llm_models(
    current_user: User = Depends(get_current_user),
    provider_name: str | None = Query(None, description="Filter models by provider name"),
    provider_enabled: bool | None = Query(None, description="Filter models by provider enabled status"),
    llm_enabled: bool | None = Query(True, description="Filter models by enabled status"),
):
    """List all supported LLM models"""
    # Import litellm here to avoid event loop warnings
    import litellm
    
    db = ad.common.get_async_db()

    # Retrieve providers from MongoDB
    cursor = db.llm_providers.find({})

    # Get all enabled providers
    providers = await cursor.to_list(length=None)

    # Get all available models for each provider
    llm_models = []
    for provider in providers:
        # Skip disabled providers
        if provider_enabled and not provider["enabled"]:
            continue

        if provider_name and provider_name != provider["litellm_provider"]:
            continue

        # Which models to return?
        if llm_enabled:
            models = provider["litellm_models_enabled"]
        else:
            models = provider["litellm_models_available"]

        # Get the max input and output tokens for each model
        for model in models:
            max_input_tokens = 0
            max_output_tokens = 0
            input_cost_per_token = 0
            output_cost_per_token = 0
            
            if model in litellm.model_cost:
                max_input_tokens = litellm.model_cost[model].get("max_input_tokens", 0)
                max_output_tokens = litellm.model_cost[model].get("max_output_tokens", 0)
                input_cost_per_token = litellm.model_cost[model].get("input_cost_per_token", 0)
                output_cost_per_token = litellm.model_cost[model].get("output_cost_per_token", 0)

            llm_model = LLMModel(
                litellm_model=model,
                litellm_provider=provider["litellm_provider"],
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
                input_cost_per_token=input_cost_per_token,
                output_cost_per_token=output_cost_per_token,
            )
            llm_models.append(llm_model)
            logger.info(f"llm_model: {llm_model}")

    return ListLLMModelsResponse(models=llm_models)

@app.get("/v0/account/llm/providers", response_model=ListLLMProvidersResponse, tags=["account/llm"])
async def list_llm_providers(
    current_user: User = Depends(get_admin_user)
):
    """List all supported LLM providers"""
    db = ad.common.get_async_db()
    
    # Retrieve models from MongoDB
    cursor = db.llm_providers.find({})
    providers = await cursor.to_list(length=None)
    
    # Convert MongoDB documents to LLMModel instances
    llm_providers = []
    for provider in providers:
        logger.info(f"provider: {provider}")

        token = provider["token"]
        if len(token) > 0:
            token = ad.crypto.decrypt_token(token)
            if len(token) > 16:
                token = token[:16] + "******"
            elif len(token) > 0:
                token = "******"
        else:
            token = None

        llm_providers.append(LLMProvider(
            name=provider["name"],
            display_name=provider["display_name"],
            litellm_provider=provider["litellm_provider"],
            litellm_models_enabled=provider["litellm_models_enabled"],
            litellm_models_available=provider["litellm_models_available"],
            enabled=provider["enabled"],
            token=token,
            token_created_at=provider["token_created_at"]
        ))
    
    return ListLLMProvidersResponse(providers=llm_providers)

@app.put("/v0/account/llm/provider/{provider_name}", tags=["account/llm"])
async def set_llm_provider_config(
    provider_name: str,
    request: SetLLMProviderConfigRequest,
    current_user: User = Depends(get_admin_user)
):
    """Set LLM provider config"""

    db = ad.common.get_async_db()

    if provider_name is None:
        raise HTTPException(status_code=400, detail="Provider name is required")

    # Get the litellm_provider from the name
    elem = await db.llm_providers.find_one({"name": provider_name})
    if elem is None:
        raise HTTPException(status_code=400, detail=f"Provider '{provider_name}' not found")
    litellm_provider = elem["litellm_provider"]
    
    if request.litellm_models_enabled is not None:
        if len(request.litellm_models_enabled) > 0:
            for model in request.litellm_models_enabled:
                if model not in elem["litellm_models_available"]:
                    raise HTTPException(status_code=400, detail=f"Model '{model}' is not available for provider '{provider_name}'")
        
        # Reorder the list
        litellm_models_enabled_ordered = sorted(request.litellm_models_enabled,
                                                key=lambda x: elem["litellm_models_available"].index(x))
    
        # Save the reordered list
        elem["litellm_models_enabled"] = litellm_models_enabled_ordered

    if request.enabled is not None:
        elem["enabled"] = request.enabled
    if request.token is not None:
        if len(request.token) > 0:
            elem["token"] = ad.crypto.encrypt_token(request.token)
            elem["token_created_at"] = datetime.now(UTC)
        else:
            elem["token_created_at"] = None

    # Save the updated provider
    await db.llm_providers.update_one(
        {"name": provider_name},
        {"$set": elem}
    )

    return {"message": "LLM provider config updated successfully"}

@app.post("/v0/account/aws_config", tags=["account/aws_config"])
async def create_aws_config(
    config: AWSConfig,
    current_user: User = Depends(get_admin_user)
):  
    """Create or update AWS configuration (admin only)"""
    db = ad.common.get_async_db()
    # Validate all fields are non-empty
    if not config.access_key_id or not config.access_key_id.strip():
        raise HTTPException(
            status_code=400,
            detail="AWS Access Key ID is required and cannot be empty"
        )
    
    if not config.secret_access_key or not config.secret_access_key.strip():
        raise HTTPException(
            status_code=400,
            detail="AWS Secret Access Key is required and cannot be empty"
        )
    
    if not config.s3_bucket_name or not config.s3_bucket_name.strip():
        raise HTTPException(
            status_code=400,
            detail="S3 Bucket Name is required and cannot be empty"
        )

    # Validate AWS Access Key ID format
    if not re.match(r'^[A-Z0-9]{20}$', config.access_key_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid AWS Access Key ID format. Must be 20 characters long and contain only uppercase letters and numbers."
        )

    # Validate AWS Secret Access Key format
    if not re.match(r'^[A-Za-z0-9+/]{40}$', config.secret_access_key):
        raise HTTPException(
            status_code=400,
            detail="Invalid AWS Secret Access Key format. Must be 40 characters long and contain only letters, numbers, and +/."
        )

    encrypted_access_key = ad.crypto.encrypt_token(config.access_key_id)
    encrypted_secret_key = ad.crypto.encrypt_token(config.secret_access_key)
    
    update_data = {
        "access_key_id": encrypted_access_key,
        "secret_access_key": encrypted_secret_key,
        "s3_bucket_name": config.s3_bucket_name,
        "created_at": datetime.now(UTC)
    }

    await db.aws_config.update_one(
        {"user_id": current_user.user_id},
        {"$set": update_data},
        upsert=True
    )
    
    return {"message": "AWS configuration saved successfully"}

@app.get("/v0/account/aws_config", tags=["account/aws_config"])
async def get_aws_config(current_user: User = Depends(get_admin_user)):
    """Get AWS configuration (admin only)"""
    db = ad.common.get_async_db()
    config = await db.aws_config.find_one({"user_id": current_user.user_id})
    if not config:
        raise HTTPException(status_code=404, detail="AWS configuration not found")
        
    return {
        "access_key_id": ad.crypto.decrypt_token(config["access_key_id"]),
        "secret_access_key": ad.crypto.decrypt_token(config["secret_access_key"]),
        "s3_bucket_name": config.get("s3_bucket_name")
    }

@app.delete("/v0/account/aws_config", tags=["account/aws_config"])
async def delete_aws_config(current_user: User = Depends(get_admin_user)):
    """Delete AWS configuration (admin only)"""
    db = ad.common.get_async_db()
    result = await db.aws_config.delete_one({"user_id": current_user.user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="AWS configuration not found")
    return {"message": "AWS configuration deleted successfully"}

@app.get("/v0/account/organizations", response_model=ListOrganizationsResponse, tags=["account/organizations"])
async def list_organizations(
    user_id: str | None = Query(None, description="Filter organizations by user ID"),
    organization_id: str | None = Query(None, description="Get a specific organization by ID"),
    name_search: str | None = Query(None, description="Case-insensitive search on organization name"),
    member_search: str | None = Query(None, description="Case-insensitive search on member name or email"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user)
):
    """
    List organizations or get a specific organization.
    - If organization_id is provided, returns just that organization
    - If user_id is provided, returns organizations for that user
    - Otherwise returns all organizations (admin only)
    - user_id and organization_id are mutually exclusive
    """
    logger.debug(f"list_organizations(): user_id: {user_id} organization_id: {organization_id} current_user: {current_user}")
    db = ad.common.get_async_db()
    is_sys_admin = await is_system_admin(current_user.user_id)

    # user_id and organization_id are mutually exclusive
    if user_id and organization_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot specify both user_id and organization_id"
        )

    # Handle single organization request
    if organization_id:
        try:
            organization = await db.organizations.find_one({"_id": ObjectId(organization_id)})
        except:
            raise HTTPException(status_code=404, detail="Organization not found")
            
        if not organization:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Check permissions
        is_org_admin = await is_organization_admin(organization_id, current_user.user_id)

        if not (is_sys_admin or is_org_admin):
            raise HTTPException(
                status_code=403,
                detail="Not authorized to view this organization"
            )

        return ListOrganizationsResponse(organizations=[
            Organization(**{
                **organization,
                "id": str(organization["_id"]),
                "type": organization["type"]
            })
        ], total_count=1, skip=0)

    # Handle user filter
    if user_id:
        if not is_sys_admin and user_id != current_user.user_id:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to view other users' organizations"
            )
        filter_user_id = user_id
    else:
        filter_user_id = None if is_sys_admin else current_user.user_id

    # Build base filters
    and_filters = []
    if filter_user_id:
        and_filters.append({"members.user_id": filter_user_id})

    # Apply organization name search
    if name_search:
        and_filters.append({"name": {"$regex": name_search, "$options": "i"}})

    # Apply member name/email search
    if member_search:
        # Find users matching by name or email
        user_cursor = db.users.find({
            "$or": [
                {"name": {"$regex": member_search, "$options": "i"}},
                {"email": {"$regex": member_search, "$options": "i"}},
            ]
        }, {"_id": 1})
        matching_users = await user_cursor.to_list(None)
        matching_user_ids = [str(u["_id"]) for u in matching_users]
        if matching_user_ids:
            and_filters.append({"members.user_id": {"$in": matching_user_ids}})
        else:
            # No users matched; return empty result fast
            return ListOrganizationsResponse(organizations=[], total_count=0, skip=skip)

    final_query = {"$and": and_filters} if and_filters else {}

    total_count = await db.organizations.count_documents(final_query)
    cursor = db.organizations.find(final_query).sort("_id", -1).skip(skip).limit(limit)
    organizations = await cursor.to_list(None)

    ret = ListOrganizationsResponse(organizations=[
        Organization(**{
            **org,
            "id": str(org["_id"]),
            "type": org["type"]
        }) for org in organizations
    ], total_count=total_count, skip=skip)
    return ret

@app.post("/v0/account/organizations", response_model=Organization, tags=["account/organizations"])
async def create_organization(
    organization: OrganizationCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a new organization"""
    db = ad.common.get_async_db()

    # Check total organizations limit
    total_orgs = await db.organizations.count_documents({})
    if total_orgs >= limits.MAX_TOTAL_ORGANIZATIONS:
        raise HTTPException(
            status_code=403,
            detail="System limit reached: Maximum number of organizations exceeded"
        )

    # Check user's organization limit
    user_orgs = await db.organizations.count_documents({
        "members.user_id": current_user.user_id
    })
    if user_orgs >= limits.MAX_ORGANIZATIONS_PER_USER:
        raise HTTPException(
            status_code=403,
            detail=f"User limit reached: Cannot be member of more than {limits.MAX_ORGANIZATIONS_PER_USER} organizations"
        )

    # Check for existing organization with same name (case-insensitive)
    existing = await db.organizations.find_one({
        "name": {"$regex": f"^{organization.name}$", "$options": "i"}
    })
    
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"An organization named '{organization.name}' already exists"
        )

    # If creating enterprise organization, verify system admin
    if organization.type == "enterprise":
        if not await is_system_admin(current_user.user_id):
            raise HTTPException(
                status_code=403,
                detail="Only system administrators can create Enterprise organizations"
            )

    organization_doc = {
        "name": organization.name,
        "members": [{
            "user_id": current_user.user_id,
            "role": "admin"
        }],
        "type": organization.type or "team",  # Default to team if not specified
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC)
    }
    
    result = await db.organizations.insert_one(organization_doc)
    org_id = str(result.inserted_id)

    # Create corresponding payments customer (and Stripe if configured)
    await sync_customer(db=db, org_id=org_id)

    return Organization(**{
        **organization_doc,
        "id": org_id
    })

@app.put("/v0/account/organizations/{organization_id}", response_model=Organization, tags=["account/organizations"])
async def update_organization(
    organization_id: str,
    organization_update: OrganizationUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update an organization (account admin or organization admin)"""
    logger.info(f"Updating organization {organization_id} with {organization_update}")
    db = ad.common.get_async_db()

    organization = await db.organizations.find_one({"_id": ObjectId(organization_id)})
    if not organization:
        logger.error(f"Organization not found: {organization_id}")
        raise HTTPException(status_code=404, detail="Organization not found")

    # Check if user has permission (account admin or organization admin)
    is_sys_admin = await is_system_admin(current_user.user_id)
    is_org_admin = await is_organization_admin(organization_id, current_user.user_id)
    
    if not (is_sys_admin or is_org_admin):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update this organization"
        )

    # Is the type changing?
    if organization_update.type is not None and organization_update.type != organization["type"]:
        logger.info(f"Updating organization type from {organization['type']} to {organization_update.type}")
        await organizations.update_organization_type(
            db=db,
            organization_id=organization_id,
            update=organization_update,
            current_user_id=current_user.user_id
        )
        # Re-fetch organization after type update to get latest state
        organization = await db.organizations.find_one({"_id": ObjectId(organization_id)})
        if not organization:
            logger.error(f"Organization not found after type update: {organization_id}")
            raise HTTPException(status_code=404, detail="Organization not found")

    update_data = {}
    if organization_update.name is not None:
        update_data["name"] = organization_update.name
    
    if organization_update.members is not None:
        # Ensure at least one admin remains
        if not any(m.role == "admin" for m in organization_update.members):
            logger.error(f"Organization must have at least one admin: {organization_update.members}")
            raise HTTPException(
                status_code=400,
                detail="Organization must have at least one admin"
            )
        update_data["members"] = [m.dict() for m in organization_update.members]

    if update_data:
        update_data["updated_at"] = datetime.now(UTC)
        # Use find_one_and_update instead of update_one to get the updated document atomically
        updated_organization = await db.organizations.find_one_and_update(
            {"_id": ObjectId(organization_id)},
            {"$set": update_data},
            return_document=True  # Return the updated document
        )
        
        if not updated_organization:
            logger.error(f"Organization not found after update: {organization_id}")
            raise HTTPException(status_code=404, detail="Organization not found")
    else:
        # If no updates were needed, just return the current organization
        updated_organization = organization

    # Update the payments customer (and Stripe if configured)
    await sync_customer(db=db, org_id=organization_id)

    return Organization(**{
        "id": str(updated_organization["_id"]),
        "name": updated_organization["name"],
        "members": updated_organization["members"],
        "type": updated_organization["type"],
        "created_at": updated_organization["created_at"],
        "updated_at": updated_organization["updated_at"]
    })

@app.delete("/v0/account/organizations/{organization_id}", tags=["account/organizations"])
async def delete_organization(
    organization_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete an organization (account admin or organization admin)"""
    # Get organization and verify it exists
    db = ad.common.get_async_db()
    organization = await db.organizations.find_one({"_id": ObjectId(organization_id)})
    if not organization:
        raise HTTPException(404, "Organization not found")
    
    # Check if user has permission (account admin or organization admin)
    is_sys_admin = await is_system_admin(current_user.user_id)
    is_org_admin = await is_organization_admin(organization_id, current_user.user_id)
    
    if not (is_sys_admin or is_org_admin):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this organization"
        )

    # Delete the payments customer
    await delete_payments_customer(db=db, org_id=organization_id)
        
    await db.organizations.delete_one({"_id": ObjectId(organization_id)})
    return {"status": "success"}

# Add these new endpoints after the existing ones
@app.get("/v0/account/users", response_model=ListUsersResponse, tags=["account/users"])
async def list_users(
    organization_id: str | None = Query(None, description="Filter users by organization ID"),
    user_id: str | None = Query(None, description="Get a specific user by ID"),
    search_name: str | None = Query(None, description="Case-insensitive search in name or email"),
    skip: int = Query(0, description="Number of users to skip"),
    limit: int = Query(10, description="Number of users to return"),
    current_user: User = Depends(get_current_user)
):
    """
    List users or get a specific user.
    - If user_id is provided, returns just that user (requires proper permissions)
    - If organization_id is provided, returns users from that organization
    - Otherwise returns all users (admin only)
    - user_id and organization_id are mutually exclusive
    """
    logger.debug(f"list_users(): organization_id: {organization_id} user_id: {user_id} current_user: {current_user} skip: {skip} limit: {limit}")
    
    db = ad.common.get_async_db()
    is_sys_admin = await is_system_admin(current_user.user_id)

    # user_id and organization_id are mutually exclusive
    if user_id and organization_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot specify both user_id and organization_id"
        )

    # Build filters and compose final query at the end
    and_filters = []
    
    # Handle single user request
    if user_id:
        try:
            user = await db.users.find_one({"_id": ObjectId(user_id)})
        except:
            raise HTTPException(status_code=404, detail="User not found")
            
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Check permissions
        is_self = current_user.user_id == user_id
        
        if not (is_sys_admin or is_self):
            # Check if user is an org admin for any org the target user is in
            user_orgs = await db.organizations.find({
                "members.user_id": user_id
            }).to_list(None)
            
            is_org_admin = any(
                any(m["user_id"] == current_user.user_id and m["role"] == "admin" 
                    for m in org["members"])
                for org in user_orgs
            )
            
            if not is_org_admin:
                raise HTTPException(
                    status_code=403,
                    detail="Not authorized to view this user"
                )
                
        ret = ListUsersResponse(
            users=[UserResponse(
                id=str(user["_id"]),
                email=user["email"],
                name=user.get("name"),
                role=user.get("role", "user"),
                email_verified=user.get("email_verified"),
                created_at=user.get("created_at", datetime.now(UTC)),
                has_password=bool(user.get("password"))
            )],
            total_count=1,
            skip=0
        )
        return ret

    # Handle organization filter
    if organization_id:
        org = await db.organizations.find_one({"_id": ObjectId(organization_id)})
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
            
        is_org_admin = any(
            m["user_id"] == current_user.user_id and m["role"] == "admin" 
            for m in org["members"]
        )
        
        if not (is_sys_admin or is_org_admin):
            raise HTTPException(
                status_code=403, 
                detail="Not authorized to view organization users"
            )
            
        member_ids = [m["user_id"] for m in org["members"]]
        and_filters.append({"_id": {"$in": [ObjectId(uid) for uid in member_ids]}})
    elif not is_sys_admin:
        # List all users in organizations the current user is an admin of
        orgs = await db.organizations.find({
            "members.user_id": current_user.user_id,
            "members.role": "admin"
        }).to_list(None)
        member_ids = [m["user_id"] for org in orgs for m in org["members"]]
        
        # Add the current user to the list of users, if they are not already in the list
        if current_user.user_id not in member_ids:
            member_ids.append(current_user.user_id)
        
        and_filters.append({"_id": {"$in": [ObjectId(uid) for uid in member_ids]}})
    else:
        # A system admin can list all users. No need to filter by organization.
        pass

    # Apply search_name filter if provided (search across entire user collection)
    if search_name:
        or_filter = {
            "$or": [
                {"name": {"$regex": search_name, "$options": "i"}},
                {"email": {"$regex": search_name, "$options": "i"}},
            ]
        }
        and_filters.append(or_filter)

    final_query = {"$and": and_filters} if and_filters else {}

    total_count = await db.users.count_documents(final_query)
    users = await db.users.find(final_query).skip(skip).limit(limit).to_list(None)

    ret = ListUsersResponse(
        users=[
            UserResponse(
                id=str(user["_id"]),
                email=user["email"],
                name=user.get("name"),
                role=user.get("role", "user"),
                email_verified=user.get("email_verified"),
                created_at=user.get("created_at", datetime.now(UTC)),
                has_password=bool(user.get("password"))
            )
            for user in users
        ],
        total_count=total_count,
        skip=skip
    )
    return ret

@app.post("/v0/account/users", response_model=UserResponse, tags=["account/users"])
async def create_user(
    user: UserCreate,
    current_user: User = Depends(get_admin_user)
):
    """Create a new user (admin only)"""
    db = ad.common.get_async_db()
    # Check total users limit
    total_users = await db.users.count_documents({})
    if total_users >= limits.MAX_TOTAL_USERS:
        raise HTTPException(
            status_code=403,
            detail="System limit reached: Maximum number of users exceeded"
        )

    # Check if email already exists
    if await db.users.find_one({"email": user.email}):
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )

    # Check if an organization exists with same name as the user email
    existing_org = await db.organizations.find_one({"name": user.email})
    if existing_org:
        raise HTTPException(
            status_code=400,
            detail=f"An organization with the same name as the user email ({user.email}) already exists"
        )
    
    # Hash password
    hashed_password = hashpw(user.password.encode(), gensalt(12))
    
    # Create user document with default role
    user_doc = {
        "email": user.email,
        "name": user.name,
        "password": hashed_password.decode(),
        "role": "user",  # Always set default role as user
        "email_verified": True,
        "created_at": datetime.now(UTC),
        "has_seen_tour": False
    }
    
    result = await db.users.insert_one(user_doc)
    user_doc["id"] = str(result.inserted_id)
    user_doc["has_password"] = True

    logger.info(f"Created new user {user.email} with id {user_doc['id']}")
    
    # Create default individual organization for new user
    result = await db.organizations.insert_one({
        "name": user.email,
        "members": [{
            "user_id": str(result.inserted_id),
            "role": "admin"
        }],
        "type": "individual",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC)
    })

    org_id = str(result.inserted_id)
    logger.info(f"Created new organization {user.email} with id {org_id}")

    # Sync the organization (local and Stripe if configured)
    await sync_customer(db=db, org_id=org_id)
    
    return UserResponse(**user_doc)

@app.put("/v0/account/users/{user_id}", response_model=UserResponse, tags=["account/users"])
async def update_user(
    user_id: str,
    user: UserUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update a user's details (admin or self)"""
    db = ad.common.get_async_db()
    # Check if user has permission (admin or self)
    is_sys_admin = await is_system_admin(current_user.user_id)
    is_self = current_user.user_id == user_id
    
    if not (is_sys_admin or is_self):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update this user"
        )
    
    # For self-updates, only allow name changes
    update_data = {}
    if is_self and not is_sys_admin:
        if user.name is not None:
            update_data["name"] = user.name
        if user.password is not None:
            update_data["password"] = hashpw(user.password.encode(), gensalt(12)).decode()
        if user.has_seen_tour is not None:
            update_data["has_seen_tour"] = user.has_seen_tour
    else:
        # Admin can update all fields
        update_data = {
            k: v for k, v in user.model_dump().items() 
            if v is not None
        }
        
        # If password is included, hash it
        if "password" in update_data:
            update_data["password"] = hashpw(update_data["password"].encode(), gensalt(12)).decode()
        
        # Don't allow updating the last admin user to non-admin
        if user.role == "user":
            admin_count = await db.users.count_documents({"role": "admin"})
            target_user = await db.users.find_one({"_id": ObjectId(user_id)})
            if admin_count == 1 and target_user and target_user.get("role") == "admin":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove admin role from the last admin user"
                )
    
    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="No valid update data provided"
        )
    
    result = await db.users.find_one_and_update(
        {"_id": ObjectId(user_id)},
        {"$set": update_data},
        return_document=True
    )
    
    if not result:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )
    
    return UserResponse(
        id=str(result["_id"]),
        email=result["email"],
        name=result.get("name"),
        role=result.get("role", "user"),
        email_verified=result.get("email_verified"),
        created_at=result.get("created_at", datetime.now(UTC)),
        has_password=bool(result.get("password"))
    )


@app.delete("/v0/account/users/{user_id}", tags=["account/users"])
async def delete_user(
    user_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a user (admin or self)"""
    db = ad.common.get_async_db()

    # Check if user has permission (admin or self)
    is_sys_admin = await is_system_admin(current_user.user_id)
    is_self = current_user.user_id == user_id
    
    if not (is_sys_admin or is_self):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this user"
        )

    # Is the user an admin of any organization?
    orgs = await db.organizations.find({"members.user_id": user_id}).to_list(None)
    if orgs:
        for org in orgs:
            # Is this a single-user organization?
            members = org["members"]
            if len(members) == 1:
                logger.info(f"Deleting single-user organization {org['_id']}")
                # Delete the organization
                await db.organizations.delete_one({"_id": org["_id"]})
                await delete_payments_customer(db, org_id=org["_id"])
                continue
            
            # Is this the last admin of the organization?
            admin_count = 0
            is_org_admin = False
            for member in members:
                if member["role"] == "admin":
                    admin_count += 1
                    if member["user_id"] == user_id:
                        is_org_admin = True
            if is_org_admin and admin_count == 1:
                raise HTTPException(
                    status_code=400,
                    detail="User is the last admin of an organization and cannot be deleted"
                )
            
            # Remove the user from the organization
            logger.info(f"Removing user {user_id} from organization {org['_id']}")
            await db.organizations.update_one(
                {"_id": org["_id"]},
                {"$pull": {"members": {"user_id": user_id}}}
            )

            # Update the payments customer (and Stripe if configured)
            await sync_customer(db=db, org_id=org["_id"])
    
    # Don't allow deleting the last admin user
    target_user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not target_user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )
    
    if target_user.get("role") == "admin":
        admin_count = await db.users.count_documents({"role": "admin"})
        if admin_count == 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last system admin user"
            )
    
    try:
        await users.delete_user(db, user_id)
        return {"message": "User and related data deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to delete user and related data"
        )

@app.post("/v0/account/email/verification/register/{user_id}", tags=["account/email"])
async def send_registration_verification_email(user_id: str):
    """Send verification email for newly registered users (no auth required)"""
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)

    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.get("email_verified"):
        raise HTTPException(status_code=400, detail="Email already verified")

    # Generate verification token
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(hours=24)).replace(tzinfo=UTC)
    
    # Store verification token
    await db.email_verifications.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "token": token,
                "expires": expires,
                "email": user["email"]
            }
        },
        upsert=True
    )
        
    verification_url = f"{NEXTAUTH_URL}/auth/verify-email?token={token}"
    
    html_content = email_utils.get_verification_email_content(
        verification_url=verification_url,
        site_url=NEXTAUTH_URL,
        user_name=user.get("name")
    )
        
    # Use the send_email utility function instead of direct SES client call
    await email_utils.send_email(
        analytiq_client,
        to_email=user["email"],
        from_email=SES_FROM_EMAIL,
        subject=email_utils.get_email_subject("verification"),
        content=html_content,
    )
    
    return {"message": "Registration email sent"}


@app.post("/v0/account/email/verification/send/{user_id}", tags=["account/email"])
async def send_verification_email(
    user_id: str,
    current_user: User = Depends(get_admin_user)
):
    """Send verification email (admin only)"""
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)

    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.get("email_verified"):
        raise HTTPException(status_code=400, detail="Email already verified")

    # Generate verification token
    token = secrets.token_urlsafe(32)
    # Ensure expiration time is stored with UTC timezone
    expires = (datetime.now(UTC) + timedelta(hours=24)).replace(tzinfo=UTC)
    
    # Store verification token
    await db.email_verifications.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "token": token,
                "expires": expires,
                "email": user["email"]
            }
        },
        upsert=True
    )
        
    verification_url = f"{NEXTAUTH_URL}/auth/verify-email?token={token}"
    
    # Get email content from template
    html_content = email_utils.get_verification_email_content(
        verification_url=verification_url,
        site_url=NEXTAUTH_URL,
        user_name=user.get("name")
    )
        
    # Use the send_email utility function instead of direct SES client call
    await email_utils.send_email(
        analytiq_client,
        to_email=user["email"],
        from_email=SES_FROM_EMAIL,
        subject=email_utils.get_email_subject("verification"),
        content=html_content,
    )

    return {"message": "Verification email sent"}

@app.post("/v0/account/email/verification/{token}", tags=["account/email"])
async def verify_email(token: str, background_tasks: BackgroundTasks):
    """Verify email address using token"""
    logger.info(f"Verifying email with token: {token}")
    db = ad.common.get_async_db()

    # Find verification record
    verification = await db.email_verifications.find_one({"token": token})
    if not verification:
        logger.info(f"No verification record found for token: {token}")
        raise HTTPException(status_code=400, detail="Invalid verification token")
        
    # Check if token expired
    # Convert stored expiration to UTC for comparison
    stored_expiry = verification["expires"].replace(tzinfo=UTC)
    if stored_expiry < datetime.now(UTC):
        logger.info(f"Verification token expired: {stored_expiry} < {datetime.now(UTC)}")
        raise HTTPException(status_code=400, detail="Verification token expired")
    
    # Update user's email verification status
    updated_user = await db.users.find_one_and_update(
        {"_id": ObjectId(verification["user_id"])},
        {"$set": {"email_verified": True}},
        return_document=True
    )

    if not updated_user:
        logger.info(f"Failed to verify email for user {verification['user_id']}")
        raise HTTPException(status_code=404, detail="User not found")

    # Ensure a default individual organization exists for the verified user
    # If none exists, create one and sync payments customer
    existing_org = await db.organizations.find_one({
        "members.user_id": str(updated_user["_id"]),
        "type": "individual"
    })
    if not existing_org:
        org_insert_result = await db.organizations.insert_one({
            "name": updated_user.get("email"),
            "members": [{
                "user_id": str(updated_user["_id"]),
                "role": "admin"
            }],
            "type": "individual",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC)
        })
        logger.info(f"Created default individual organization for user {updated_user['_id']}")
        try:
            await sync_customer(db=db, org_id=str(org_insert_result.inserted_id))
        except Exception as e:
            logger.warning(f"Failed to sync payments customer for org {org_insert_result.inserted_id}: {e}")
    else:
        logger.info(f"Default individual organization already exists for user {updated_user['_id']}")

    # Allow the user to re-verify their email for 1 minute
    logger.info(f"Scheduling deletion of verification record for token: {token}")
    async def delete_verification_later():
        await asyncio.sleep(60)  # Wait 60 seconds
        await db.email_verifications.delete_one({"token": token})
        logger.info(f"Deleted verification record for token: {token}")

    background_tasks.add_task(delete_verification_later)
    
    return {"message": "Email verified successfully"}

@app.post("/v0/account/email/invitations", response_model=InvitationResponse, tags=["account/email"])
async def create_invitation(
    invitation: CreateInvitationRequest,
    current_user: User = Depends(get_admin_user)
):
    """Create a new invitation (admin only)"""
    logger.info(f"Got invitation request: {invitation}")
    analytiq_client = ad.common.get_analytiq_client()
    db = ad.common.get_async_db(analytiq_client)

    # Check if email already registered, if so, set user_exists to True
    existing_user = await db.users.find_one({"email": invitation.email})
    if existing_user:
        user_exists = True
    else:
        user_exists = False
    
    if user_exists:
        if invitation.organization_id:
            # Check if user is already in the organization
            org = await db.organizations.find_one({
                "_id": ObjectId(invitation.organization_id),
                "members.user_id": existing_user["_id"]
            })
            if org:
                raise HTTPException(status_code=400, detail="User is already a member of this organization")
        else:
            # User already exists, and this is not an org invitation
            raise HTTPException(status_code=400, detail="User already exists")

    if await db.users.find_one({"email": invitation.email}):
        raise HTTPException(
            status_code=400,
            detail="Email already registered for invitation"
        )
        
    # If there's an existing pending invitation for the same organization, invalidate it
    query = {
        "email": invitation.email,
        "status": "pending",
        "expires": {"$gt": datetime.now(UTC)}
    }
    
    # Only add organization_id to query if it exists
    if invitation.organization_id:
        query["organization_id"] = invitation.organization_id
    else:
        # If this is not an org invitation, only invalidate other non-org invitations
        query["organization_id"] = {"$exists": False}

    await db.invitations.update_many(
        query,
        {"$set": {"status": "invalidated"}}
    )

    # Generate invitation token
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=24)
    
    # Create invitation document
    invitation_doc = {
        "email": invitation.email,
        "token": token,
        "status": "pending",
        "expires": expires,
        "created_by": current_user.user_id,
        "created_at": datetime.now(UTC),
        "user_exists": user_exists
    }
    
    # Get organization name if this is an org invitation
    organization_name = None
    if invitation.organization_id:
        invitation_doc["organization_id"] = invitation.organization_id
        
        org = await db.organizations.find_one({"_id": ObjectId(invitation.organization_id)})
        if org:
            organization_name = org["name"]
    
    result = await db.invitations.insert_one(invitation_doc)
    invitation_doc["id"] = str(result.inserted_id)
    
    # Send invitation email
    invitation_url = f"{NEXTAUTH_URL}/auth/accept-invitation?token={token}"
    html_content = email_utils.get_invitation_email_content(
        invitation_url=invitation_url,
        site_url=NEXTAUTH_URL,
        expires=expires,
        organization_name=organization_name
    )
    
    try:
        # Send invitation email using the email_utils.send_email() utility function
        await email_utils.send_email(
            analytiq_client,
            to_email=invitation.email,
            from_email=SES_FROM_EMAIL,
            subject=email_utils.get_email_subject("invitation"),
            content=html_content,
        )
        return InvitationResponse(**invitation_doc)
    except Exception as e:
        logger.error(f"Failed to send invitation email: {str(e)}")
        # Delete invitation if email fails
        await db.invitations.delete_one({"_id": result.inserted_id})
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send invitation email: {str(e)}"
        )

@app.get("/v0/account/email/invitations", response_model=ListInvitationsResponse, tags=["account/email"])
async def list_invitations(
    skip: int = Query(0),
    limit: int = Query(10),
    current_user: User = Depends(get_admin_user)
):
    """List all invitations (admin only)"""
    db = ad.common.get_async_db()
    query = {}
    total_count = await db.invitations.count_documents(query)
    cursor = db.invitations.find(query).skip(skip).limit(limit)
    invitations = await cursor.to_list(None)
    
    return ListInvitationsResponse(
        invitations=[
            InvitationResponse(
                id=str(inv["_id"]),
                email=inv["email"],
                status=inv["status"],
                expires=inv["expires"],
                created_by=inv["created_by"],
                created_at=inv["created_at"],
                organization_id=inv.get("organization_id"),
                organization_role=inv.get("organization_role")
            )
            for inv in invitations
        ],
        total_count=total_count,
        skip=skip
    )

@app.get("/v0/account/email/invitations/{token}", response_model=InvitationResponse, tags=["account/email"])
async def get_invitation(token: str):
    """Get invitation details by token"""
    db = ad.common.get_async_db()
    invitation = await db.invitations.find_one({
        "token": token,
        "status": "pending"
    })
    
    if not invitation:
        raise HTTPException(
            status_code=404,
            detail="Invalid or expired invitation"
        )
        
    # Ensure both datetimes are timezone-aware for comparison
    invitation_expires = invitation["expires"].replace(tzinfo=UTC)
    if invitation_expires < datetime.now(UTC):
        await db.invitations.update_one(
            {"_id": invitation["_id"]},
            {"$set": {"status": "expired"}}
        )
        raise HTTPException(
            status_code=400,
            detail="Invitation has expired"
        )

    # Check if user already exists
    user_exists = await db.users.find_one({"email": invitation["email"]}) is not None

    # Get organization name if this is an org invitation
    organization_name = None
    if invitation.get("organization_id"):
        org = await db.organizations.find_one({"_id": ObjectId(invitation["organization_id"])})
        if org:
            organization_name = org["name"]
    
    return InvitationResponse(
        id=str(invitation["_id"]),
        email=invitation["email"],
        status=invitation["status"],
        expires=invitation["expires"],
        created_by=invitation["created_by"],
        created_at=invitation["created_at"],
        organization_id=invitation.get("organization_id"),
        organization_name=organization_name,  # Add organization name
        user_exists=user_exists  # Add user existence status
    )

@app.post("/v0/account/email/invitations/{token}/accept", tags=["account/email"])
async def accept_invitation(
    token: str,
    data: AcceptInvitationRequest = Body(...)  # Change to use AcceptInvitationRequest
):
    """Accept an invitation and create user account if needed"""
    logger.info(f"Accepting invitation with token: {token}")
    db = ad.common.get_async_db()
    # Find and validate invitation
    invitation = await db.invitations.find_one({
        "token": token,
        "status": "pending"
    })
    
    if not invitation:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired invitation"
        )
        
    # Ensure both datetimes are timezone-aware for comparison
    invitation_expires = invitation["expires"].replace(tzinfo=UTC)
    if invitation_expires < datetime.now(UTC):
        await db.invitations.update_one(
            {"_id": invitation["_id"]},
            {"$set": {"status": "expired"}}
        )
        raise HTTPException(
            status_code=400,
            detail="Invitation has expired"
        )
    
    # Check if user already exists
    existing_user = await db.users.find_one({"email": invitation["email"]})
    
    if existing_user:
        user_id = str(existing_user["_id"])
        
        # If this is an organization invitation, add user to the organization
        if invitation.get("organization_id"):
            # Check if user is already in the organization
            org = await db.organizations.find_one({
                "_id": ObjectId(invitation["organization_id"]),
                "members.user_id": user_id
            })
            
            if org:
                raise HTTPException(
                    status_code=400,
                    detail="User is already a member of this organization"
                )
            
            # Add user to organization
            await db.organizations.update_one(
                {"_id": ObjectId(invitation["organization_id"])},
                {
                    "$push": {
                        "members": {
                            "user_id": user_id,
                            "role": "user"  # Default all invited users to regular member role
                        }
                    }
                }
            )
        
        # Mark invitation as accepted
        await db.invitations.update_one(
            {"_id": invitation["_id"]},
            {"$set": {"status": "accepted"}}
        )
        
        return {"message": "User added to organization successfully"}
    
    # Create new user account if they don't exist
    if not data.name or not data.password:
        raise HTTPException(
            status_code=400,
            detail="Name and password are required for new accounts"
        )

    hashed_password = hashpw(data.password.encode(), gensalt(12))
    user_doc = {
        "email": invitation["email"],
        "name": data.name,
        "password": hashed_password.decode(),
        "role": "user",  # Default all invited users to regular user role
        "email_verified": True,  # Auto-verify since it's from invitation
        "created_at": datetime.now(UTC)
    }
    
    try:
        result = await db.users.insert_one(user_doc)
        user_id = str(result.inserted_id)
        
        # If organization invitation, add to organization
        if invitation.get("organization_id"):
            org_id = invitation["organization_id"]
            await db.organizations.update_one(
                {"_id": ObjectId(org_id)},
                {
                    "$push": {
                        "members": {
                            "user_id": user_id,
                            "role": "user"  # Default all invited users to regular member role
                        }
                    }
                }
            )
        else:
            # Create default individual organization
            result = await db.organizations.insert_one({
                "name": invitation["email"],
                "members": [{
                    "user_id": user_id,
                    "role": "admin"  # User is admin of their individual org
                }],
                "type": "individual",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC)
            })

            org_id = str(result.inserted_id)

        # Sync the organization (local and Stripe if configured)
        await sync_customer(db=db, org_id=org_id)
        
        # Mark invitation as accepted
        await db.invitations.update_one(
            {"_id": invitation["_id"]},
            {"$set": {"status": "accepted"}}
        )
        
        return {"message": "Account created successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create account: {str(e)}"
        )

# Account-level access tokens
@app.post("/v0/account/access_tokens", response_model=AccessToken, tags=["account/access_tokens"])
async def create_account_token(
    request: CreateAccessTokenRequest,
    current_user: User = Depends(get_current_user)
):
    """Create an account-level API token"""
    logger.debug(f"Creating account token for user: {current_user} request: {request}")
    db = ad.common.get_async_db()

    # Generate unique token - retry if encrypted token already exists
    max_retries = 10
    for attempt in range(max_retries):
        token = secrets.token_urlsafe(32)
        encrypted_token = ad.crypto.encrypt_token(token)
        
        # Check if this encrypted token already exists
        existing_token = await db.access_tokens.find_one({"token": encrypted_token})
        if not existing_token:
            break
        
        if attempt == max_retries - 1:
            raise HTTPException(
                status_code=500, 
                detail="Unable to generate unique token after multiple attempts"
            )
    
    new_token = {
        "user_id": current_user.user_id,
        "organization_id": None,  # Explicitly set to None for account-level tokens
        "name": request.name,
        "token": encrypted_token,
        "created_at": datetime.now(UTC),
        "lifetime": request.lifetime
    }
    result = await db.access_tokens.insert_one(new_token)

    new_token["token"] = token  # Return plaintext token to user
    new_token["id"] = str(result.inserted_id)
    return new_token

@app.get("/v0/account/access_tokens", response_model=ListAccessTokensResponse, tags=["account/access_tokens"])
async def list_account_tokens(
    current_user: User = Depends(get_current_user)
):
    """List account-level API tokens"""
    db = ad.common.get_async_db()
    
    cursor = db.access_tokens.find({
        "user_id": current_user.user_id,
        "organization_id": None  # Only get account-level tokens
    })
    tokens = await cursor.to_list(length=None)
    ret = [
        {
            "id": str(token["_id"]),
            "user_id": token["user_id"],
            "organization_id": None,
            "name": token["name"],
            "token": token["token"],
            "created_at": token["created_at"],
            "lifetime": token["lifetime"]
        }
        for token in tokens
    ]
    return ListAccessTokensResponse(access_tokens=ret)

@app.delete("/v0/account/access_tokens/{token_id}", tags=["account/access_tokens"])
async def delete_account_token(
    token_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete an account-level API token"""
    db = ad.common.get_async_db()
    
    result = await db.access_tokens.delete_one({
        "_id": ObjectId(token_id),
        "user_id": current_user.user_id,
        "organization_id": None  # Only delete account-level tokens
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"message": "Token deleted successfully"}

app.include_router(payments_router)

# --- Form Schema Versioning Helper ---
async def get_form_id_and_version(form_id: Optional[str] = None) -> tuple[str, int]:
    """
    Get the next version for an existing form schema or create a new form schema identifier.
    Args:
        form_id: Existing form schema ID, or None to create a new one
    Returns:
        Tuple of (form_id, form_version)
    """
    db = ad.common.get_async_db()
    if form_id is None:
        # Insert a placeholder document to get MongoDB-generated ID
        result = await db.forms.insert_one({
            "form_version": 1
        })
        form_id = str(result.inserted_id)
        form_version = 1
    else:
        # Get the next version for an existing form schema
        result = await db.forms.find_one_and_update(
            {"_id": ObjectId(form_id)},
            {"$inc": {"form_version": 1}},
            upsert=True,
            return_document=True
        )
        form_version = result["form_version"]
    return form_id, form_version
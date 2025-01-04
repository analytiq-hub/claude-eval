# main.py

from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Depends, status, Body, Security, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, timedelta, UTC
from jose import JWTError, jwt
from typing import Optional, List
import os
import sys
import json
from dotenv import load_dotenv
import secrets
import base64
import io
import re
import uuid
import logging
import hmac
import hashlib
from bcrypt import hashpw, gensalt

import startup
import organizations
from schemas import (
    User,
    AccessToken, ListAccessTokensResponse, CreateAccessTokenRequest,
    ListDocumentsResponse,
    DocumentMetadata,
    DocumentUpload, DocumentsUpload, DocumentUpdate,
    LLMToken, CreateLLMTokenRequest, ListLLMTokensResponse,
    AWSCredentials,
    OCRMetadataResponse,
    LLMRunResponse, LLMResult,
    Schema, SchemaCreate, ListSchemasResponse,
    Prompt, PromptCreate, ListPromptsResponse,
    TagCreate, Tag, ListTagsResponse,
    DocumentResponse,
    UserCreate, UserUpdate, UserResponse, ListUsersResponse,
    OrganizationMember,
    OrganizationCreate,
    OrganizationUpdate,
    Organization,
    ListOrganizationsResponse
)

# Set up the path
cwd = os.path.dirname(os.path.abspath(__file__))
sys.path.append(f"{cwd}/..")

import analytiq_data as ad
import users

# Set up the environment variables. This reads the .env file.
ad.common.setup()

# Environment variables
ENV = os.getenv("ENV", "dev")
NEXTAUTH_URL = os.getenv("NEXTAUTH_URL")
FASTAPI_ROOT_PATH = os.getenv("FASTAPI_ROOT_PATH", "/")
MONGODB_URI = os.getenv("MONGODB_URI")
SES_FROM_EMAIL = os.getenv("SES_FROM_EMAIL")

ad.log.info(f"ENV: {ENV}")
ad.log.info(f"NEXTAUTH_URL: {NEXTAUTH_URL}")
ad.log.info(f"FASTAPI_ROOT_PATH: {FASTAPI_ROOT_PATH}")
ad.log.info(f"MONGODB_URI: {MONGODB_URI}")
ad.log.info(f"SES_FROM_EMAIL: {SES_FROM_EMAIL}")
# JWT settings
FASTAPI_SECRET = os.getenv("FASTAPI_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
UPLOAD_DIR = "data"

app = FastAPI(
    root_path=FASTAPI_ROOT_PATH,
)
security = HTTPBearer()

# CORS configuration
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    NEXTAUTH_URL,
]

ad.log.info(f"CORS allowed origins: {origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"] # Needed to expose the Content-Disposition header to the frontend
)

# MongoDB connection
analytiq_client = ad.common.get_analytiq_client(env=ENV)
db_name = ENV
db = analytiq_client.mongodb_async[db_name]
job_queue_collection = db.job_queue
access_token_collection = db.access_tokens
llm_token_collection = db.llm_tokens
aws_credentials_collection = db.aws_credentials
schemas_collection = db.schemas
schema_versions_collection = db.schema_versions
prompts_collection = db.prompts
prompt_versions_collection = db.prompt_versions
tags_collection = db.tags
organizations_collection = db.organizations

from pydantic import BaseModel

# Modify get_current_user to validate userId in database
async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    try:
        # First, try to validate as JWT
        payload = jwt.decode(token, FASTAPI_SECRET, algorithms=[ALGORITHM])
        userId: str = payload.get("userId")
        userName: str = payload.get("userName")
        email: str = payload.get("email")
        ad.log.debug(f"get_current_user(): userId: {userId}, userName: {userName}, email: {email}")
        if userName is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        
        # Validate that userId exists in database
        user = await db.users.find_one({"_id": ObjectId(userId)})
        if not user:
            raise HTTPException(status_code=401, detail="User not found in database")
            
        return User(user_id=userId,
                    user_name=userName,
                    token_type="jwt")
                   
    except JWTError:
        ad.log.debug(f"get_current_user(): JWT validation failed")
        # If JWT validation fails, check if it's an API token
        encrypted_token = ad.crypto.encrypt_token(token)
        stored_token = await access_token_collection.find_one({"token": encrypted_token})
        
        if stored_token:
            # Validate that user_id from stored token exists in database
            user = await db.users.find_one({"_id": ObjectId(stored_token["user_id"])})
            if not user:
                raise HTTPException(status_code=401, detail="User not found in database")
                
            return User(
                user_id=stored_token["user_id"],
                user_name=stored_token["name"],
                token_type="api"
            )
                
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

# Add this helper function to check admin status
async def get_admin_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    user = await get_current_user(credentials)
    
    # Check if user has admin role in database
    db_user = await db.users.find_one({"_id": ObjectId(user.user_id)})
    if not db_user or db_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )
    return user

# Add to startup
@app.on_event("startup")
async def startup_event():
    await startup.setup_admin(analytiq_client)
    await startup.setup_api_creds(analytiq_client)

# PDF management endpoints
@app.post("/documents")
async def upload_document(
    documents_upload: DocumentsUpload = Body(...),
    current_user: User = Depends(get_current_user)
):
    """Upload one or more documents"""
    ad.log.debug(f"upload_document(): documents: {[doc.name for doc in documents_upload.files]}")
    uploaded_documents = []

    # Validate all tag IDs first
    all_tag_ids = set()
    for document in documents_upload.files:
        all_tag_ids.update(document.tag_ids)
    
    if all_tag_ids:
        # Check if all tags exist and belong to the user
        tags_cursor = tags_collection.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in all_tag_ids]},
            "created_by": current_user.user_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = all_tag_ids - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )

    for document in documents_upload.files:
        if not document.name.endswith('.pdf'):
            raise HTTPException(status_code=400, detail=f"Document {document.name} is not a PDF")
        
        # Decode and save the document
        content = base64.b64decode(document.content.split(',')[1])

        # Create a unique id for the document
        document_id = ad.common.create_id()
        mongo_file_name = f"{document_id}.pdf"

        metadata = {
            "document_id": document_id,
            "type": "application/pdf",
            "size": len(content),
            "user_file_name": document.name
        }

        # Save the document to mongodb
        ad.common.save_file(analytiq_client,
                            file_name=mongo_file_name,
                            blob=content,
                            metadata=metadata)

        document_metadata = {
            "_id": ObjectId(document_id),
            "user_file_name": document.name,
            "mongo_file_name": mongo_file_name,
            "document_id": document_id,
            "upload_date": datetime.utcnow(),
            "uploaded_by": current_user.user_name,
            "state": ad.common.doc.DOCUMENT_STATE_UPLOADED,
            "tag_ids": document.tag_ids  # Add tags to the document metadata
        }
        
        await ad.common.save_doc(analytiq_client, document_metadata)
        uploaded_documents.append({
            "document_name": document.name,
            "document_id": document_id,
            "tag_ids": document.tag_ids
        })

        # Post a message to the ocr job queue
        msg = {"document_id": document_id}
        await ad.queue.send_msg(analytiq_client, "ocr", msg=msg)
    
    return {"uploaded_documents": uploaded_documents}

@app.put("/documents/{document_id}")
async def update_document(
    document_id: str,
    update: DocumentUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update a document"""
    ad.log.debug(f"Updating document {document_id} with data: {update}")

    # Validate the document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if document["uploaded_by"] != current_user.user_name:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to modify this document"
        )

    # Validate all tag IDs
    if update.tag_ids:
        tags_cursor = tags_collection.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in update.tag_ids]},
            "created_by": current_user.user_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = set(update.tag_ids) - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )

    # Update the document
    result = await db.docs.update_one(
        {"_id": ObjectId(document_id)},
        {"$set": {"tag_ids": update.tag_ids}}
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Document not found or no changes made"
        )

    return {"message": "Document tags updated successfully"}


@app.get("/documents", response_model=ListDocumentsResponse)
async def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    tag_ids: str = Query(None, description="Comma-separated list of tag IDs"),
    user: User = Depends(get_current_user)
):
    """List documents"""
    # Build the query filter
    query_filter = {}
    
    # Add tag filtering if tag_ids are provided
    if tag_ids:
        tag_id_list = [tid.strip() for tid in tag_ids.split(",")]
        query_filter["tag_ids"] = {"$all": tag_id_list}
    
    # Get total count with filters
    total_count = await db.docs.count_documents(query_filter)
    
    # Get paginated documents with sorting and filters
    cursor = db.docs.find(query_filter).sort("_id", -1).skip(skip).limit(limit)
    documents = await cursor.to_list(length=None)
    
    return ListDocumentsResponse(
        documents=[
            {
                "id": str(doc["_id"]),
                "document_name": doc["user_file_name"],
                "upload_date": doc["upload_date"].isoformat(),
                "uploaded_by": doc["uploaded_by"],
                "state": doc.get("state", ""),
                "tag_ids": doc.get("tag_ids", [])
            }
            for doc in documents
        ],
        total_count=total_count,
        skip=skip
    )

@app.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get a document"""
    ad.log.debug(f"get_document() start: document_id: {document_id}")
    document = await ad.common.get_doc(analytiq_client, document_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
        
    ad.log.debug(f"get_document() found document: {document}")

    # Get the file from mongodb
    file = ad.common.get_file(analytiq_client, document["mongo_file_name"])
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    ad.log.debug(f"get_document() got file: {document}")

    # Create metadata response
    metadata = DocumentMetadata(
        id=str(document["_id"]),
        document_name=document["user_file_name"],
        upload_date=document["upload_date"],
        uploaded_by=document["uploaded_by"],
        state=document.get("state", ""),
        tag_ids=document.get("tag_ids", [])
    )

    # Return using the DocumentResponse model
    return DocumentResponse(
        metadata=metadata,
        content=base64.b64encode(file["blob"]).decode('utf-8')
    )

@app.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a document"""
    document = await ad.common.get_doc(analytiq_client, document_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if "mongo_file_name" not in document:
        raise HTTPException(
            status_code=500, 
            detail="Document metadata is corrupted: missing mongo_file_name"
        )

    ad.common.delete_file(analytiq_client, file_name=document["mongo_file_name"])
    await ad.common.delete_doc(analytiq_client, document_id)

    return {"message": "Document deleted successfully"}

@app.post("/access_tokens", response_model=AccessToken)
async def access_token_create(
    request: CreateAccessTokenRequest,
    current_user: User = Depends(get_current_user)
):
    """Create an API token"""
    ad.log.debug(f"Creating API token for user: {current_user} request: {request}")
    token = secrets.token_urlsafe(32)
    new_token = {
        "user_id": current_user.user_id,
        "name": request.name,
        "token": ad.crypto.encrypt_token(token),  # Store encrypted token
        "created_at": datetime.now(UTC),
        "lifetime": request.lifetime
    }
    result = await access_token_collection.insert_one(new_token)

    # Return the new token with the id
    new_token["token"] = token  # Return plaintext token to user
    new_token["id"] = str(result.inserted_id)
    return new_token

@app.get("/access_tokens", response_model=ListAccessTokensResponse)
async def access_token_list(current_user: User = Depends(get_current_user)):
    """List API tokens"""
    cursor = access_token_collection.find({"user_id": current_user.user_id})
    tokens = await cursor.to_list(length=None)
    ret = [
        {
            "id": str(token["_id"]),
            "user_id": token["user_id"],
            "name": token["name"],
            "token": token["token"],
            "created_at": token["created_at"],
            "lifetime": token["lifetime"]
        }
        for token in tokens
    ]
    ad.log.debug(f"list_access_tokens(): {ret}")
    return ListAccessTokensResponse(access_tokens=ret)

@app.delete("/access_tokens/{token_id}")
async def access_token_delete(
    token_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete an API token"""
    result = await access_token_collection.delete_one({
        "_id": ObjectId(token_id),
        "user_id": current_user.user_id
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"message": "Token deleted successfully"}

@app.post("/auth/token")
async def create_auth_token(user_data: dict = Body(...)):
    """Create an authentication token"""
    ad.log.debug(f"create_auth_token(): user_data: {user_data}")
    token = jwt.encode(
        {
            "userId": user_data["id"],
            "userName": user_data["name"],
            "email": user_data["email"]
        },
        FASTAPI_SECRET,
        algorithm=ALGORITHM
    )
    return {"token": token}

@app.get("/ocr/download/blocks/{document_id}")
async def download_ocr_blocks(
    document_id: str,
    current_user: User = Depends(get_current_user)
):
    """Download OCR blocks for a document"""
    ad.log.debug(f"download_ocr_blocks() start: document_id: {document_id}")

    document = await ad.common.get_doc(analytiq_client, document_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get the OCR JSON data from mongodb
    ocr_list = ad.common.get_ocr_list(analytiq_client, document_id)
    if ocr_list is None:
        raise HTTPException(status_code=404, detail="OCR data not found")
    
    return JSONResponse(content=ocr_list)

@app.get("/ocr/download/text/{document_id}", response_model=str)
async def download_ocr_text(
    document_id: str,
    page_num: Optional[int] = Query(None, description="Specific page number to retrieve"),
    current_user: User = Depends(get_current_user)
):
    """Download OCR text for a document"""
    ad.log.debug(f"download_ocr_text() start: document_id: {document_id}, page_num: {page_num}")
    document = await ad.common.get_doc(analytiq_client, document_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Page number is 1-based, but the OCR text page_idx is 0-based
    page_idx = None
    if page_num is not None:
        page_idx = page_num - 1

    # Get the OCR text data from mongodb
    text = ad.common.get_ocr_text(analytiq_client, document_id, page_idx)
    if text is None:
        raise HTTPException(status_code=404, detail="OCR text not found")
    
    return Response(content=text, media_type="text/plain")

@app.get("/ocr/download/metadata/{document_id}", response_model=OCRMetadataResponse)
async def get_ocr_metadata(
    document_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get OCR metadata for a document"""
    ad.log.debug(f"get_ocr_metadata() start: document_id: {document_id}")
    
    document = await ad.common.get_doc(analytiq_client, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get the OCR metadata from mongodb
    metadata = ad.common.get_ocr_metadata(analytiq_client, document_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="OCR metadata not found")
    
    return OCRMetadataResponse(
        n_pages=metadata["n_pages"],
        ocr_date=metadata["ocr_date"].isoformat()
    )

# LLM Run Endpoints
@app.post("/llm/run/{document_id}", response_model=LLMRunResponse)
async def run_llm_analysis(
    document_id: str,
    prompt_id: str = Query(default="default", description="The prompt ID to use"),
    force: bool = Query(default=False, description="Force new run even if result exists"),
    current_user: User = Depends(get_current_user)
):
    """
    Run LLM on a document, with optional force refresh.
    """
    ad.log.debug(f"run_llm_analysis() start: document_id: {document_id}, prompt_id: {prompt_id}, force: {force}")
    
    # Verify document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Verify OCR is complete
    ocr_metadata = ad.common.get_ocr_metadata(analytiq_client, document_id)
    if ocr_metadata is None:
        raise HTTPException(status_code=404, detail="OCR metadata not found")

    try:
        result = await ad.llm.run_llm(
            analytiq_client,
            document_id=document_id,
            prompt_id=prompt_id,
            force=force
        )
        
        return LLMRunResponse(
            status="success",
            result=result
        )
        
    except Exception as e:
        ad.log.error(f"Error in LLM run: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing document: {str(e)}"
        )

@app.get("/llm/result/{document_id}", response_model=LLMResult)
async def get_llm_result(
    document_id: str,
    prompt_id: str = Query(default="default", description="The prompt ID to retrieve"),
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve existing LLM results for a document.
    """
    ad.log.debug(f"get_llm_result() start: document_id: {document_id}, prompt_id: {prompt_id}")
    
    # Verify document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    result = await ad.llm.get_llm_result(analytiq_client, document_id, prompt_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"LLM result not found for document_id: {document_id} and prompt_id: {prompt_id}"
        )
    
    return result

@app.delete("/llm/result/{document_id}")
async def delete_llm_result(
    document_id: str,
    prompt_id: str = Query(..., description="The prompt ID to delete"),
    current_user: User = Depends(get_current_user)
):
    """
    Delete LLM results for a specific document and prompt.
    """
    ad.log.debug(f"delete_llm_result() start: document_id: {document_id}, prompt_id: {prompt_id}")
    
    # Verify document exists and user has access
    document = await ad.common.get_doc(analytiq_client, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    deleted = await ad.llm.delete_llm_result(analytiq_client, document_id, prompt_id)
    
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"LLM result not found for document_id: {document_id} and prompt_id: {prompt_id}"
        )
    
    return {"status": "success", "message": "LLM result deleted"}

# Add this helper function near the top of the file with other functions
async def get_next_schema_version(schema_name: str) -> int:
    """Atomically get the next version number for a schema"""
    result = await schema_versions_collection.find_one_and_update(
        {"_id": schema_name},
        {"$inc": {"version": 1}},
        upsert=True,
        return_document=True
    )
    return result["version"]

# Schema management endpoints
@app.post("/schemas", response_model=Schema)
async def create_schema(
    schema: SchemaCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a schema"""
    # Check if schema with this name already exists (case-insensitive)
    existing_schema = await schemas_collection.find_one({
        "name": {"$regex": f"^{schema.name}$", "$options": "i"}
    })
    
    # If schema exists, treat this as an update operation
    if existing_schema:
        # Get the next version
        new_version = await get_next_schema_version(schema.name)
        
        # Create new version of the schema
        schema_dict = {
            "name": existing_schema["name"],  # Use existing name to preserve case
            "fields": [field.model_dump() for field in schema.fields],
            "version": new_version,
            "created_at": datetime.utcnow(),
            "created_by": current_user.user_id
        }
    else:
        # This is a new schema
        new_version = await get_next_schema_version(schema.name)
        schema_dict = {
            "name": schema.name,
            "fields": [field.model_dump() for field in schema.fields],
            "version": new_version,
            "created_at": datetime.utcnow(),
            "created_by": current_user.user_id
        }
    
    # Insert into MongoDB
    result = await schemas_collection.insert_one(schema_dict)
    
    # Return complete schema
    schema_dict["id"] = str(result.inserted_id)
    return Schema(**schema_dict)

@app.get("/schemas", response_model=ListSchemasResponse)
async def list_schemas(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user)
):
    """List schemas"""
    # Build the base pipeline
    pipeline = [
        {
            "$sort": {"_id": -1}
        },
        {
            "$group": {
                "_id": "$name",
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
    
    result = await schemas_collection.aggregate(pipeline).to_list(length=1)
    result = result[0]
    
    total_count = result["total"][0]["count"] if result["total"] else 0
    schemas = result["schemas"]
    
    # Convert _id to id in each schema
    for schema in schemas:
        schema['id'] = str(schema.pop('_id'))
    
    return ListSchemasResponse(
        schemas=schemas,
        total_count=total_count,
        skip=skip
    )

@app.get("/schemas/{schema_id}", response_model=Schema)
async def get_schema(
    schema_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get a schema"""
    schema = await schemas_collection.find_one({"_id": ObjectId(schema_id)})
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found")
    schema['id'] = str(schema.pop('_id'))
    # version is already included from MongoDB doc, no need to add it
    return Schema(**schema)

@app.put("/schemas/{schema_id}", response_model=Schema)
async def update_schema(
    schema_id: str,
    schema: SchemaCreate,
    current_user: User = Depends(get_current_user)
):
    """Update a schema"""
    # Get the existing schema
    existing_schema = await schemas_collection.find_one({"_id": ObjectId(schema_id)})
    if not existing_schema:
        raise HTTPException(status_code=404, detail="Schema not found")
    
    # Check if user has permission to update
    if existing_schema["created_by"] != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this schema")
    
    # Validate field names
    is_valid, error_msg = validate_schema_fields(schema.fields)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=error_msg
        )
    
    # Atomically get the next version number
    new_version = await get_next_schema_version(existing_schema["name"])
    
    # Create new version of the schema
    new_schema = {
        "name": schema.name,
        "fields": [field.model_dump() for field in schema.fields],
        "version": new_version,
        "created_at": datetime.utcnow(),
        "created_by": current_user.user_id
    }
    
    # Insert new version
    result = await schemas_collection.insert_one(new_schema)
    
    # Return updated schema
    new_schema["id"] = str(result.inserted_id)
    return Schema(**new_schema)

@app.delete("/schemas/{schema_id}")
async def delete_schema(
    schema_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a schema"""
    # Get the schema to find its name
    schema = await schemas_collection.find_one({"_id": ObjectId(schema_id)})
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found")
    
    if schema["created_by"] != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this schema")
    
    # Check for dependent prompts
    dependent_prompts = await prompts_collection.find({
        "schema_name": schema["name"]
    }).to_list(length=None)
    
    if dependent_prompts:
        # Format the list of dependent prompts
        prompt_list = [
            {"name": p["name"], "version": p["version"]} 
            for p in dependent_prompts
        ]
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete schema because it has dependent prompts:{json.dumps(prompt_list)}"
        )
    
    # If no dependent prompts, proceed with deletion
    result = await schemas_collection.delete_many({"name": schema["name"]})
    
    # Also delete the version counter
    await schema_versions_collection.delete_one({"_id": schema["name"]})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Schema not found")
    return {"message": "Schema deleted successfully"}

# Add this validation function
def validate_schema_fields(fields: list) -> tuple[bool, str]:
    field_names = [field.name.lower() for field in fields]
    seen = set()
    for name in field_names:
        if name in seen:
            return False, f"Duplicate field name: {name}"
        seen.add(name)
    return True, ""

# Add this helper function near get_next_schema_version
async def get_next_prompt_version(prompt_name: str) -> int:
    """Atomically get the next version number for a prompt"""
    result = await prompt_versions_collection.find_one_and_update(
        {"_id": prompt_name},
        {"$inc": {"version": 1}},
        upsert=True,
        return_document=True
    )
    return result["version"]

# Prompt management endpoints
@app.post("/prompts", response_model=Prompt)
async def create_prompt(
    prompt: PromptCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a prompt"""
    # Only verify schema if one is specified
    if prompt.schema_name and prompt.schema_version:
        schema = await schemas_collection.find_one({
            "name": prompt.schema_name,
            "version": prompt.schema_version
        })
        if not schema:
            raise HTTPException(
                status_code=404,
                detail=f"Schema {prompt.schema_name} version {prompt.schema_version} not found"
            )

    # Validate tag IDs if provided
    if prompt.tag_ids:
        tags_cursor = tags_collection.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in prompt.tag_ids]},
            "created_by": current_user.user_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = set(prompt.tag_ids) - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )

    # Check if prompt with this name already exists (case-insensitive)
    existing_prompt = await prompts_collection.find_one({
        "name": {"$regex": f"^{prompt.name}$", "$options": "i"}
    })
    
    # Get the next version
    new_version = await get_next_prompt_version(prompt.name)
    
    # Create prompt document
    prompt_dict = {
        "name": existing_prompt["name"] if existing_prompt else prompt.name,
        "content": prompt.content,
        "schema_name": prompt.schema_name or "",
        "schema_version": prompt.schema_version or 0,
        "version": new_version,
        "created_at": datetime.utcnow(),
        "created_by": current_user.user_id,
        "tag_ids": prompt.tag_ids  # Add tag_ids to the document
    }
    
    # Insert into MongoDB
    result = await prompts_collection.insert_one(prompt_dict)
    
    # Return complete prompt
    prompt_dict["id"] = str(result.inserted_id)
    return Prompt(**prompt_dict)

@app.get("/prompts", response_model=ListPromptsResponse)
async def list_prompts(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    document_id: str = Query(None, description="Filter prompts by document's tags"),
    tag_ids: str = Query(None, description="Comma-separated list of tag IDs"),
    current_user: User = Depends(get_current_user)
):
    """List prompts"""
    # Build the base pipeline
    pipeline = []
    
    # Add document tag filtering if document_id is provided
    if document_id:
        document = await db.docs.find_one({"_id": ObjectId(document_id)})
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        document_tag_ids = document.get("tag_ids", [])
        if document_tag_ids:
            pipeline.append({
                "$match": {"tag_ids": {"$in": document_tag_ids}}
            })
    # Add direct tag filtering if tag_ids are provided
    elif tag_ids:
        tag_id_list = [tid.strip() for tid in tag_ids.split(",")]
        pipeline.append({
            "$match": {"tag_ids": {"$all": tag_id_list}}
        })
    
    # Add the rest of the pipeline stages
    pipeline.extend([
        {
            "$sort": {"_id": -1}
        },
        {
            "$group": {
                "_id": "$name",
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
                "prompts": [
                    {"$skip": skip},
                    {"$limit": limit}
                ]
            }
        }
    ])
    
    result = await prompts_collection.aggregate(pipeline).to_list(length=1)
    result = result[0]
    
    total_count = result["total"][0]["count"] if result["total"] else 0
    prompts = result["prompts"]
    
    # Convert _id to id in each prompt
    for prompt in prompts:
        prompt['id'] = str(prompt.pop('_id'))
    
    return ListPromptsResponse(
        prompts=prompts,
        total_count=total_count,
        skip=skip
    )

@app.get("/prompts/{prompt_id}", response_model=Prompt)
async def get_prompt(
    prompt_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get a prompt"""
    prompt = await prompts_collection.find_one({"_id": ObjectId(prompt_id)})
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    prompt['id'] = str(prompt.pop('_id'))
    return Prompt(**prompt)

@app.put("/prompts/{prompt_id}", response_model=Prompt)
async def update_prompt(
    prompt_id: str,
    prompt: PromptCreate,
    current_user: User = Depends(get_current_user)
):
    """Update a prompt"""
    # Get the existing prompt
    existing_prompt = await prompts_collection.find_one({"_id": ObjectId(prompt_id)})
    if not existing_prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    # Check if user has permission to update
    if existing_prompt["created_by"] != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this prompt")
    
    # Only verify schema if one is specified
    if prompt.schema_name and prompt.schema_version:
        schema = await schemas_collection.find_one({
            "name": prompt.schema_name,
            "version": prompt.schema_version
        })
        if not schema:
            raise HTTPException(
                status_code=404,
                detail=f"Schema {prompt.schema_name} version {prompt.schema_version} not found"
            )
    
    # Validate tag IDs if provided
    if prompt.tag_ids:
        tags_cursor = tags_collection.find({
            "_id": {"$in": [ObjectId(tag_id) for tag_id in prompt.tag_ids]},
            "created_by": current_user.user_id
        })
        existing_tags = await tags_cursor.to_list(None)
        existing_tag_ids = {str(tag["_id"]) for tag in existing_tags}
        
        invalid_tags = set(prompt.tag_ids) - existing_tag_ids
        if invalid_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag IDs: {list(invalid_tags)}"
            )
    
    # Get the next version number
    new_version = await get_next_prompt_version(existing_prompt["name"])
    
    # Create new version of the prompt
    new_prompt = {
        "name": prompt.name,
        "content": prompt.content,
        "schema_name": prompt.schema_name or "",  # Use empty string if None
        "schema_version": prompt.schema_version or 0,  # Use 0 if None
        "version": new_version,
        "created_at": datetime.utcnow(),
        "created_by": current_user.user_id,
        "tag_ids": prompt.tag_ids  # Add tag_ids to the document
    }
    
    # Insert new version
    result = await prompts_collection.insert_one(new_prompt)
    
    # Return updated prompt
    new_prompt["id"] = str(result.inserted_id)
    return Prompt(**new_prompt)

@app.delete("/prompts/{prompt_id}")
async def delete_prompt(
    prompt_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a prompt"""
    # Get the prompt to find its name
    prompt = await prompts_collection.find_one({"_id": ObjectId(prompt_id)})
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    if prompt["created_by"] != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this prompt")
    
    # Delete all versions of this prompt
    result = await prompts_collection.delete_many({"name": prompt["name"]})
    
    # Also delete the version counter
    await prompt_versions_collection.delete_one({"_id": prompt["name"]})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"message": "Prompt deleted successfully"}

# Tag management endpoints
@app.post("/tags", response_model=Tag)
async def create_tag(
    tag: TagCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a tag"""
    # Check if tag with this name already exists for this user
    existing_tag = await tags_collection.find_one({
        "name": tag.name,
        "created_by": current_user.user_id
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
        "created_by": current_user.user_id
    }
    
    result = await tags_collection.insert_one(new_tag)
    new_tag["id"] = str(result.inserted_id)
    
    return Tag(**new_tag)

@app.get("/tags", response_model=ListTagsResponse)
async def list_tags(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user)
):
    """List tags"""
    # Get total count
    total_count = await tags_collection.count_documents({"created_by": current_user.user_id})
    
    # Get paginated tags with sorting by _id in descending order
    cursor = tags_collection.find(
        {"created_by": current_user.user_id}
    ).sort("_id", -1).skip(skip).limit(limit)  # Sort by _id descending (newest first)
    
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

@app.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a tag"""
    # Verify tag exists and belongs to user
    tag = await tags_collection.find_one({
        "_id": ObjectId(tag_id),
        "created_by": current_user.user_id
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
    prompts_with_tag = await prompts_collection.find_one({
        "tag_ids": tag_id
    })
    
    if prompts_with_tag:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete tag '{tag['name']}' because it is assigned to one or more prompts"
        )
    
    result = await tags_collection.delete_one({
        "_id": ObjectId(tag_id),
        "created_by": current_user.user_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    return {"message": "Tag deleted successfully"}

@app.put("/tags/{tag_id}", response_model=Tag)
async def update_tag(
    tag_id: str,
    tag: TagCreate,
    current_user: User = Depends(get_current_user)
):
    """Update a tag"""
    # Verify tag exists and belongs to user
    existing_tag = await tags_collection.find_one({
        "_id": ObjectId(tag_id),
        "created_by": current_user.user_id
    })
    
    if not existing_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Keep the original name, only update color and description
    update_data = {
        "color": tag.color,
        "description": tag.description
    }
    
    result = await tags_collection.update_one(
        {"_id": ObjectId(tag_id)},
        {"$set": update_data}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Tag not found or not modified")
    
    # Get and return the updated tag
    updated_tag = await tags_collection.find_one({"_id": ObjectId(tag_id)})
    return Tag(**{**updated_tag, "id": str(updated_tag["_id"])})

@app.post("/account/llm_tokens", response_model=LLMToken)
async def llm_token_create(
    request: CreateLLMTokenRequest,
    current_user: User = Depends(get_admin_user)
):
    """Create or update an LLM token (admin only)"""
    ad.log.debug(f"Creating/Updating LLM token for user: {current_user} request: {request}")
    
    # Check if a token for this vendor already exists
    existing_token = await llm_token_collection.find_one({
        "user_id": current_user.user_id,
        "llm_vendor": request.llm_vendor
    })

    new_token = {
        "user_id": current_user.user_id,
        "llm_vendor": request.llm_vendor,
        "token": ad.crypto.encrypt_token(request.token),
        "created_at": datetime.now(UTC),
    }

    if existing_token:
        # Update the existing token
        result = await llm_token_collection.replace_one(
            {"_id": existing_token["_id"]},
            new_token
        )
        new_token["id"] = str(existing_token["_id"])
        ad.log.debug(f"Updated existing LLM token for {request.llm_vendor}")
    else:
        # Insert a new token
        result = await llm_token_collection.insert_one(new_token)
        new_token["id"] = str(result.inserted_id)
        new_token["token"] = ad.crypto.decrypt_token(new_token["token"])
        ad.log.debug(f"Created new LLM token for {request.llm_vendor}")

    return new_token

@app.get("/account/llm_tokens", response_model=ListLLMTokensResponse)
async def llm_token_list(current_user: User = Depends(get_admin_user)):
    """List LLM tokens (admin only)"""
    cursor = llm_token_collection.find({"user_id": current_user.user_id})
    tokens = await cursor.to_list(length=None)
    llm_tokens = [
        {
            "id": str(token["_id"]),
            "user_id": token["user_id"],
            "llm_vendor": token["llm_vendor"],
            "token": ad.crypto.decrypt_token(token["token"]),
            "created_at": token["created_at"],
        }
        for token in tokens
    ]
    return ListLLMTokensResponse(llm_tokens=llm_tokens)

@app.delete("/account/llm_tokens/{token_id}")
async def llm_token_delete(
    token_id: str,
    current_user: User = Depends(get_admin_user)
):
    """Delete an LLM token (admin only)"""
    result = await llm_token_collection.delete_one({
        "_id": ObjectId(token_id),
        "user_id": current_user.user_id
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"message": "Token deleted successfully"}

@app.post("/account/aws_credentials")
async def create_aws_credentials(
    credentials: AWSCredentials,
    current_user: User = Depends(get_admin_user)
):  
    """Create or update AWS credentials (admin only)"""

    # Validate AWS Access Key ID format
    if not re.match(r'^[A-Z0-9]{20}$', credentials.access_key_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid AWS Access Key ID format. Must be 20 characters long and contain only uppercase letters and numbers."
        )

    # Validate AWS Secret Access Key format
    if not re.match(r'^[A-Za-z0-9+/]{40}$', credentials.secret_access_key):
        raise HTTPException(
            status_code=400,
            detail="Invalid AWS Secret Access Key format. Must be 40 characters long and contain only letters, numbers, and +/."
        )

    encrypted_access_key = ad.crypto.encrypt_token(credentials.access_key_id)
    encrypted_secret_key = ad.crypto.encrypt_token(credentials.secret_access_key)
    
    await aws_credentials_collection.update_one(
        {"user_id": current_user.user_id},
        {
            "$set": {
                "access_key_id": encrypted_access_key,
                "secret_access_key": encrypted_secret_key,
                "created_at": datetime.now(UTC)
            }
        },
        upsert=True
    )
    
    return {"message": "AWS credentials saved successfully"}

@app.get("/account/aws_credentials")
async def get_aws_credentials(current_user: User = Depends(get_admin_user)):
    """Get AWS credentials (admin only)"""
    credentials = await aws_credentials_collection.find_one({"user_id": current_user.user_id})
    if not credentials:
        raise HTTPException(status_code=404, detail="AWS credentials not found")
        
    return {
        "access_key_id": ad.crypto.decrypt_token(credentials["access_key_id"]),
        "secret_access_key": ad.crypto.decrypt_token(credentials["secret_access_key"])
    }

@app.delete("/account/aws_credentials")
async def delete_aws_credentials(current_user: User = Depends(get_admin_user)):
    """Delete AWS credentials (admin only)"""
    result = await aws_credentials_collection.delete_one({"user_id": current_user.user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="AWS credentials not found")
    return {"message": "AWS credentials deleted successfully"}

@app.get("/account/organizations", response_model=ListOrganizationsResponse)
async def list_organizations(
    user_id: str | None = Query(None, description="Filter organizations by user ID"),
    current_user: User = Depends(get_current_user)
):
    """List organizations (admin can see all or filter by user, users can only see their own)"""
    ad.log.info(f"Listing organizations for user {current_user.user_id}")
  
    db_user = await db.users.find_one({"_id": ObjectId(current_user.user_id)})
    is_admin = db_user and db_user.get("role") == "admin"
    
    if user_id:
        if not is_admin and user_id != current_user.user_id:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to view other users' organizations"
            )
        filter_user_id = user_id
    else:
        filter_user_id = None if is_admin else current_user.user_id

    query = {}
    if filter_user_id:
        query["members.user_id"] = filter_user_id
    
    ad.log.info(f"All organizations: {await db.organizations.find({}).to_list(None)}")
    ad.log.info(f"Listing organizations for user {current_user.user_id} with query {query}")

    organizations = await db.organizations.find(query).to_list(None)

    ad.log.info(f"Organizations: {organizations}")

    return ListOrganizationsResponse(organizations=[
        Organization(**{
            **org,
            "id": str(org["_id"]),
            "type": org["type"]
        }) for org in organizations
    ])

@app.post("/account/organizations", response_model=Organization)
async def create_organization(
    organization: OrganizationCreate,
    current_user: User = Depends(get_admin_user)
):
    """Create a new organization (admin only)"""
    # Check for existing organization with same name (case-insensitive)
    existing = await db.organizations.find_one({
        "name": {"$regex": f"^{organization.name}$", "$options": "i"}
    })
    
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"An organization named '{organization.name}' already exists"
        )

    organization_doc = {
        "name": organization.name,
        "members": [{
            "user_id": current_user.user_id,
            "role": "admin"
        }],
        "type": organization.type,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = await db.organizations.insert_one(organization_doc)
    return Organization(**{
        **organization_doc,
        "id": str(result.inserted_id)
    })

@app.put("/account/organizations/{organization_id}", response_model=Organization)
async def update_organization(
    organization_id: str,
    organization_update: OrganizationUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update an organization (account admin or organization admin)"""
    organization = await db.organizations.find_one({"_id": ObjectId(organization_id)})
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Check if user has permission (account admin or organization admin)
    db_user = await db.users.find_one({"_id": ObjectId(current_user.user_id)})
    is_account_admin = db_user and db_user.get("role") == "admin"
    is_organization_admin = any(member["role"] == "admin" and member["user_id"] == current_user.user_id for member in organization["members"])
    
    if not (is_account_admin or is_organization_admin):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update this organization"
        )

    # Is the type changing?
    if organization_update.type is not None and organization_update.type != organization["type"]:
        ad.log.info(f"Updating organization type from {organization['type']} to {organization_update.type}")
        await organizations.update_organization_type(
            db=db,
            organization_id=organization_id,
            update=organization_update
        )

    update_data = {}
    if organization_update.name is not None:
        update_data["name"] = organization_update.name
    
    if organization_update.members is not None:
        # Ensure at least one admin remains
        if not any(m.role == "admin" for m in organization_update.members):
            raise HTTPException(
                status_code=400,
                detail="Organization must have at least one admin"
            )
        update_data["members"] = [m.dict() for m in organization_update.members]

    if update_data:
        update_data["updated_at"] = datetime.now(UTC)
        result = await db.organizations.update_one(
            {"_id": ObjectId(organization_id)},
            {"$set": update_data}
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Organization not found")

    updated_organization = await db.organizations.find_one({"_id": ObjectId(organization_id)})
    return Organization(**{
        "id": str(updated_organization["_id"]),
        "name": updated_organization["name"],
        "members": updated_organization["members"],
        "type": updated_organization["type"],
        "created_at": updated_organization["created_at"],
        "updated_at": updated_organization["updated_at"]
    })

@app.delete("/account/organizations/{organization_id}")
async def delete_organization(
    organization_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete an organization (account admin or organization admin)"""
    # Get organization and verify it exists
    organization = await db.organizations.find_one({"_id": ObjectId(organization_id)})
    if not organization:
        raise HTTPException(404, "Organization not found")
    
    # Check if user has permission (account admin or organization admin)
    db_user = await db.users.find_one({"_id": ObjectId(current_user.user_id)})
    is_account_admin = db_user and db_user.get("role") == "admin"
    is_organization_admin = any(member["role"] == "admin" and member["user_id"] == current_user.user_id for member in organization["members"])
    
    if not (is_account_admin or is_organization_admin):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this organization"
        )
        
    await db.organizations.delete_one({"_id": ObjectId(organization_id)})
    return {"status": "success"}

# Add these new endpoints after the existing ones
@app.get("/account/users", response_model=ListUsersResponse)
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_admin_user)
):
    """List all users (admin only)"""
    # Get total count
    total_count = await db.users.count_documents({})
    
    # Get paginated users
    cursor = db.users.find({}).sort("_id", -1).skip(skip).limit(limit)
    users = await cursor.to_list(length=None)
    
    return ListUsersResponse(
        users=[
            UserResponse(
                id=str(user["_id"]),
                email=user["email"],
                name=user.get("name"),
                role=user.get("role", "user"),
                emailVerified=user.get("emailVerified"),
                createdAt=user.get("createdAt", datetime.now(UTC)),
                hasPassword=bool(user.get("password"))
            )
            for user in users
        ],
        total_count=total_count,
        skip=skip
    )

@app.post("/account/users", response_model=UserResponse)
async def create_user(
    user: UserCreate,
    current_user: User = Depends(get_admin_user)
):
    """Create a new user (admin only)"""
    # Check if email already exists
    if await db.users.find_one({"email": user.email}):
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )
    
    # Hash password
    hashed_password = hashpw(user.password.encode(), gensalt(12))
    
    # Create user document
    user_doc = {
        "email": user.email,
        "name": user.name,
        "password": hashed_password.decode(),
        "role": "user",
        "emailVerified": False,
        "createdAt": datetime.now(UTC)
    }
    
    result = await db.users.insert_one(user_doc)
    user_doc["id"] = str(result.inserted_id)
    user_doc["hasPassword"] = True
    
    # Create default personal organization for new user
    await db.organizations.insert_one({
        "_id": result.inserted_id,
        "name": "Default",
        "members": [{
            "user_id": str(result.inserted_id),
            "role": "admin"
        }],
        "type": "personal",  # Default organization is personal type
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    })
    
    return UserResponse(**user_doc)

@app.put("/account/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user: UserUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update a user's details (admin or self)"""
    # Check if user has permission (admin or self)
    db_current_user = await db.users.find_one({"_id": ObjectId(current_user.user_id)})
    is_admin = db_current_user.get("role") == "admin"
    is_self = current_user.user_id == user_id
    
    if not (is_admin or is_self):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update this user"
        )
    
    # For self-updates, only allow name changes
    update_data = {}
    if is_self and not is_admin:
        if user.name is not None:
            update_data["name"] = user.name
        if user.password is not None:
            update_data["password"] = hashpw(user.password.encode(), gensalt(12)).decode()
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
        emailVerified=result.get("emailVerified"),
        createdAt=result.get("createdAt", datetime.now(UTC)),
        hasPassword=bool(result.get("password"))
    )

@app.delete("/account/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a user (admin or self)"""
    # Check if user has permission (admin or self)
    db_current_user = await db.users.find_one({"_id": ObjectId(current_user.user_id)})
    is_admin = db_current_user.get("role") == "admin"
    is_self = current_user.user_id == user_id
    
    if not (is_admin or is_self):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this user"
        )
    
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
                detail="Cannot delete the last admin user"
            )
    
    try:
        await users.delete_user(db, user_id)
        return {"message": "User and related data deleted successfully"}
    except Exception as e:
        ad.log.error(f"Error deleting user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to delete user and related data"
        )

@app.get("/account/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str, 
    current_user: User = Depends(get_current_user)
):
    """Get a user's details (admin or self)"""
    # Check if user has permission (admin or self)
    db_current_user = await db.users.find_one({"_id": ObjectId(current_user.user_id)})
    is_admin = db_current_user.get("role") == "admin"
    is_self = current_user.user_id == user_id
    
    if not (is_admin or is_self):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view this user's details"
        )
    
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse(
        id=str(user["_id"]),
        email=user["email"],
        name=user.get("name"),
        role=user.get("role", "user"),
        emailVerified=user.get("emailVerified"),
        createdAt=user.get("createdAt", datetime.now(UTC)),
        hasPassword=bool(user.get("password"))
    )

@app.post("/auth/send-verification-email/{user_id}")
async def send_verification_email(
    user_id: str,
    current_user: User = Depends(get_admin_user)
):
    """Send verification email to user (admin only)"""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.get("emailVerified"):
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
        
    verification_url = f"{NEXTAUTH_URL}/verify-email?token={token}"
    
    # Send email using SES
    try:
        aws_client = ad.aws.get_aws_client(analytiq_client)
        ses_client = aws_client.session.client("ses", region_name=aws_client.region_name)

        ad.log.debug(f"SES_FROM_EMAIL: {SES_FROM_EMAIL}")
        ad.log.debug(f"ToAddresses: {user['email']}")
        ad.log.debug(f"Verification URL: {verification_url}")

        response = ses_client.send_email(
            Source=SES_FROM_EMAIL,
            Destination={
                'ToAddresses': [user["email"]]
            },
            Message={
                'Subject': {
                    'Data': 'Verify your email address'
                },
                'Body': {
                    'Html': {
                        'Data': f"""
                            <h1>Verify your email address</h1>
                            <p>Please click the link below to verify your email address:</p>
                            <p><a href="{verification_url}">{verification_url}</a></p>
                            <p>This link will expire in 24 hours.</p>
                        """
                    }
                }
            }
        )
        return {"message": "Verification email sent"}
    except Exception as e:
        ad.log.error(f"Failed to send email: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to send email: {str(e)}")

@app.post("/auth/verify-email/{token}")
async def verify_email(token: str):
    """Verify email address using token"""

    ad.log.info(f"Verifying email with token: {token}")

    # Find verification record
    verification = await db.email_verifications.find_one({"token": token})
    if not verification:
        raise HTTPException(status_code=400, detail="Invalid verification token")
        
    # Check if token expired
    # Convert stored expiration to UTC for comparison
    stored_expiry = verification["expires"].replace(tzinfo=UTC)
    if stored_expiry < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Verification token expired")
    
    # Update user's email verification status
    result = await db.users.update_one(
        {"_id": ObjectId(verification["user_id"])},
        {"$set": {"emailVerified": True}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=400, detail="Failed to verify email")
        
    # Delete verification record
    await db.email_verifications.delete_one({"token": token})
    
    return {"message": "Email verified successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="::", port=8000)

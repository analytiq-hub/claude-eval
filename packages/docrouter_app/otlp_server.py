"""
OTLP gRPC Server for organization-specific telemetry collection
"""

import asyncio
import grpc
import logging
from concurrent import futures
from typing import Dict, Optional

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2_grpc
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2_grpc

# Import the service classes will be defined in this file
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
from datetime import datetime, UTC
from bson import ObjectId
import analytiq_data as ad

logger = logging.getLogger(__name__)

# OTLP Service Classes
class OTLPTraceService(trace_service_pb2_grpc.TraceServiceServicer):
    """OTLP Trace Service for organization-specific trace collection"""
    
    def __init__(self, organization_id: str):
        self.organization_id = organization_id
    
    async def Export(self, request: ExportTraceServiceRequest, context):
        """Export traces via OTLP gRPC"""
        try:
            logger.debug(f"OTLP Export traces for org {self.organization_id}: {len(request.resource_spans)} resource spans")
            
            # Convert OTLP traces to our format
            traces = []
            for resource_span in request.resource_spans:
                # Convert protobuf to dict format
                trace_data = {
                    "resource_spans": [self._convert_resource_span(resource_span)],
                    "tag_ids": [],
                    "metadata": {"source": "otlp-grpc"}
                }
                traces.append(trace_data)
            
            # Store in database
            analytiq_client = ad.common.get_analytiq_client()
            db = ad.common.get_async_db(analytiq_client)
            
            uploaded_traces = []
            for trace_data in traces:
                trace_id = ad.common.create_id()
                
                # Calculate span count
                span_count = 0
                for resource_span in trace_data["resource_spans"]:
                    if "scope_spans" in resource_span:
                        for scope_span in resource_span["scope_spans"]:
                            if "spans" in scope_span:
                                span_count += len(scope_span["spans"])
                
                trace_metadata = {
                    "_id": ObjectId(trace_id),
                    "trace_id": trace_id,
                    "resource_spans": trace_data["resource_spans"],
                    "span_count": span_count,
                    "upload_date": datetime.now(UTC),
                    "uploaded_by": "otlp-grpc",
                    "tag_ids": trace_data["tag_ids"],
                    "metadata": trace_data["metadata"],
                    "organization_id": self.organization_id
                }
                
                await db.telemetry_traces.insert_one(trace_metadata)
                uploaded_traces.append({
                    "trace_id": trace_id,
                    "span_count": span_count,
                    "tag_ids": trace_data["tag_ids"],
                    "metadata": trace_data["metadata"]
                })
            
            return ExportTraceServiceResponse(partial_success=None)
            
        except Exception as e:
            logger.error(f"Error in OTLP trace export: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return ExportTraceServiceResponse()
    
    def _convert_resource_span(self, resource_span):
        """Convert protobuf ResourceSpan to dict format"""
        # This is a simplified conversion - you may need to handle more fields
        result = {}
        
        if resource_span.resource:
            result["resource"] = {
                "attributes": [
                    {"key": attr.key, "value": {"stringValue": attr.value.string_value}}
                    for attr in resource_span.resource.attributes
                ]
            }
        
        if resource_span.scope_spans:
            result["scope_spans"] = []
            for scope_span in resource_span.scope_spans:
                scope_data = {}
                if scope_span.scope:
                    scope_data["scope"] = {
                        "name": scope_span.scope.name,
                        "version": scope_span.scope.version
                    }
                
                if scope_span.spans:
                    scope_data["spans"] = []
                    for span in scope_span.spans:
                        span_data = {
                            "traceId": span.trace_id.hex(),
                            "spanId": span.span_id.hex(),
                            "name": span.name,
                            "kind": span.kind,
                            "startTimeUnixNano": str(span.start_time_unix_nano),
                            "endTimeUnixNano": str(span.end_time_unix_nano),
                        }
                        if span.parent_span_id:
                            span_data["parentSpanId"] = span.parent_span_id.hex()
                        if span.attributes:
                            span_data["attributes"] = [
                                {"key": attr.key, "value": {"stringValue": attr.value.string_value}}
                                for attr in span.attributes
                            ]
                        if span.status:
                            span_data["status"] = {"code": span.status.code}
                        
                        scope_data["spans"].append(span_data)
                
                result["scope_spans"].append(scope_data)
        
        return result

class OTLPMetricsService(metrics_service_pb2_grpc.MetricsServiceServicer):
    """OTLP Metrics Service for organization-specific metrics collection"""
    
    def __init__(self, organization_id: str):
        self.organization_id = organization_id
    
    async def Export(self, request: ExportMetricsServiceRequest, context):
        """Export metrics via OTLP gRPC"""
        try:
            logger.debug(f"OTLP Export metrics for org {self.organization_id}: {len(request.resource_metrics)} resource metrics")
            
            # Convert OTLP metrics to our format
            metrics = []
            for resource_metric in request.resource_metrics:
                for scope_metric in resource_metric.scope_metrics:
                    for metric in scope_metric.metrics:
                        metric_data = {
                            "name": metric.name,
                            "description": metric.description,
                            "unit": metric.unit,
                            "type": self._get_metric_type(metric),
                            "data_points": self._convert_data_points(metric),
                            "resource": self._convert_resource(resource_metric.resource),
                            "tag_ids": [],
                            "metadata": {"source": "otlp-grpc"}
                        }
                        metrics.append(metric_data)
            
            # Store in database
            analytiq_client = ad.common.get_analytiq_client()
            db = ad.common.get_async_db(analytiq_client)
            
            uploaded_metrics = []
            for metric_data in metrics:
                metric_id = ad.common.create_id()
                
                metric_metadata = {
                    "_id": ObjectId(metric_id),
                    "metric_id": metric_id,
                    "name": metric_data["name"],
                    "description": metric_data["description"],
                    "unit": metric_data["unit"],
                    "type": metric_data["type"],
                    "data_points": metric_data["data_points"],
                    "resource": metric_data["resource"],
                    "data_point_count": len(metric_data["data_points"]),
                    "upload_date": datetime.now(UTC),
                    "uploaded_by": "otlp-grpc",
                    "tag_ids": metric_data["tag_ids"],
                    "metadata": metric_data["metadata"],
                    "organization_id": self.organization_id
                }
                
                await db.telemetry_metrics.insert_one(metric_metadata)
                uploaded_metrics.append({
                    "metric_id": metric_id,
                    "name": metric_data["name"],
                    "type": metric_data["type"],
                    "data_point_count": len(metric_data["data_points"]),
                    "tag_ids": metric_data["tag_ids"],
                    "metadata": metric_data["metadata"]
                })
            
            return ExportMetricsServiceResponse(partial_success=None)
            
        except Exception as e:
            logger.error(f"Error in OTLP metrics export: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return ExportMetricsServiceResponse()
    
    def _get_metric_type(self, metric):
        """Determine metric type from protobuf"""
        if metric.HasField("gauge"):
            return "gauge"
        elif metric.HasField("sum"):
            return "counter" if metric.sum.is_monotonic else "gauge"
        elif metric.HasField("histogram"):
            return "histogram"
        elif metric.HasField("summary"):
            return "summary"
        else:
            return "unknown"
    
    def _convert_data_points(self, metric):
        """Convert metric data points to our format"""
        data_points = []
        
        if metric.HasField("gauge"):
            for dp in metric.gauge.data_points:
                data_points.append({
                    "timeUnixNano": str(dp.time_unix_nano),
                    "value": {"asDouble": dp.as_double} if dp.HasField("as_double") else {"asInt": str(dp.as_int)}
                })
        elif metric.HasField("sum"):
            for dp in metric.sum.data_points:
                data_points.append({
                    "timeUnixNano": str(dp.time_unix_nano),
                    "value": {"asDouble": dp.as_double} if dp.HasField("as_double") else {"asInt": str(dp.as_int)}
                })
        elif metric.HasField("histogram"):
            for dp in metric.histogram.data_points:
                data_points.append({
                    "timeUnixNano": str(dp.time_unix_nano),
                    "count": str(dp.count),
                    "sum": dp.sum,
                    "bucket_counts": [str(bc) for bc in dp.bucket_counts],
                    "explicit_bounds": [eb for eb in dp.explicit_bounds]
                })
        
        return data_points
    
    def _convert_resource(self, resource):
        """Convert resource to our format"""
        if not resource:
            return {}
        
        return {
            "attributes": [
                {"key": attr.key, "value": {"stringValue": attr.value.string_value}}
                for attr in resource.attributes
            ]
        }

class OTLPLogsService(logs_service_pb2_grpc.LogsServiceServicer):
    """OTLP Logs Service for organization-specific logs collection"""
    
    def __init__(self, organization_id: str):
        self.organization_id = organization_id
    
    async def Export(self, request: ExportLogsServiceRequest, context):
        """Export logs via OTLP gRPC"""
        try:
            logger.debug(f"OTLP Export logs for org {self.organization_id}: {len(request.resource_logs)} resource logs")
            
            # Convert OTLP logs to our format
            logs = []
            for resource_log in request.resource_logs:
                for scope_log in resource_log.scope_logs:
                    for log_record in scope_log.log_records:
                        log_data = {
                            "timestamp": datetime.fromtimestamp(log_record.time_unix_nano / 1_000_000_000, tz=UTC).isoformat(),
                            "severity": self._get_severity_name(log_record.severity_number),
                            "body": log_record.body.string_value if log_record.body.HasField("string_value") else "",
                            "attributes": self._convert_attributes(log_record.attributes),
                            "resource": self._convert_resource(resource_log.resource),
                            "trace_id": log_record.trace_id.hex() if log_record.trace_id else None,
                            "span_id": log_record.span_id.hex() if log_record.span_id else None,
                            "tag_ids": [],
                            "metadata": {"source": "otlp-grpc"}
                        }
                        logs.append(log_data)
            
            # Store in database
            analytiq_client = ad.common.get_analytiq_client()
            db = ad.common.get_async_db(analytiq_client)
            
            uploaded_logs = []
            for log_data in logs:
                log_id = ad.common.create_id()
                
                log_metadata = {
                    "_id": ObjectId(log_id),
                    "log_id": log_id,
                    "timestamp": datetime.fromisoformat(log_data["timestamp"].replace('Z', '+00:00')),
                    "severity": log_data["severity"],
                    "body": log_data["body"],
                    "attributes": log_data["attributes"],
                    "resource": log_data["resource"],
                    "trace_id": log_data["trace_id"],
                    "span_id": log_data["span_id"],
                    "upload_date": datetime.now(UTC),
                    "uploaded_by": "otlp-grpc",
                    "tag_ids": log_data["tag_ids"],
                    "metadata": log_data["metadata"],
                    "organization_id": self.organization_id
                }
                
                await db.telemetry_logs.insert_one(log_metadata)
                uploaded_logs.append({
                    "log_id": log_id,
                    "timestamp": log_data["timestamp"],
                    "severity": log_data["severity"],
                    "body": log_data["body"],
                    "tag_ids": log_data["tag_ids"],
                    "metadata": log_data["metadata"]
                })
            
            return ExportLogsServiceResponse(partial_success=None)
            
        except Exception as e:
            logger.error(f"Error in OTLP logs export: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return ExportLogsServiceResponse()
    
    def _get_severity_name(self, severity_number):
        """Convert severity number to name"""
        severity_map = {
            1: "TRACE", 2: "TRACE", 3: "TRACE", 4: "TRACE",
            5: "DEBUG", 6: "DEBUG", 7: "DEBUG", 8: "DEBUG",
            9: "INFO", 10: "INFO", 11: "INFO", 12: "INFO",
            13: "WARN", 14: "WARN", 15: "WARN", 16: "WARN",
            17: "ERROR", 18: "ERROR", 19: "ERROR", 20: "ERROR",
            21: "FATAL", 22: "FATAL", 23: "FATAL", 24: "FATAL"
        }
        return severity_map.get(severity_number, "INFO")
    
    def _convert_attributes(self, attributes):
        """Convert attributes to our format"""
        result = {}
        for attr in attributes:
            if attr.value.HasField("string_value"):
                result[attr.key] = attr.value.string_value
            elif attr.value.HasField("int_value"):
                result[attr.key] = attr.value.int_value
            elif attr.value.HasField("double_value"):
                result[attr.key] = attr.value.double_value
            elif attr.value.HasField("bool_value"):
                result[attr.key] = attr.value.bool_value
        return result
    
    def _convert_resource(self, resource):
        """Convert resource to our format"""
        if not resource:
            return {}
        
        result = {}
        for attr in resource.attributes:
            if attr.value.HasField("string_value"):
                result[attr.key] = attr.value.string_value
            elif attr.value.HasField("int_value"):
                result[attr.key] = attr.value.int_value
            elif attr.value.HasField("double_value"):
                result[attr.key] = attr.value.double_value
            elif attr.value.HasField("bool_value"):
                result[attr.key] = attr.value.bool_value
        return result

class OTLPServer:
    """OTLP gRPC Server with organization-specific routing"""
    
    def __init__(self, port: int = 4317):
        self.port = port
        self.server = None
        self.organization_services: Dict[str, Dict[str, any]] = {}
    
    def add_organization_services(self, organization_id: str):
        """Add OTLP services for a specific organization"""
        self.organization_services[organization_id] = {
            'trace_service': OTLPTraceService(organization_id),
            'metrics_service': OTLPMetricsService(organization_id),
            'logs_service': OTLPLogsService(organization_id)
        }
        logger.info(f"Added OTLP services for organization: {organization_id}")
    
    def remove_organization_services(self, organization_id: str):
        """Remove OTLP services for a specific organization"""
        if organization_id in self.organization_services:
            del self.organization_services[organization_id]
            logger.info(f"Removed OTLP services for organization: {organization_id}")
    
    def get_organization_from_metadata(self, context) -> Optional[str]:
        """Extract organization ID from gRPC metadata"""
        # Check for organization header in metadata
        metadata = dict(context.invocation_metadata())
        organization_id = metadata.get('organization-id')
        
        if not organization_id:
            # Try to extract from authority (host header)
            authority = metadata.get(':authority', '')
            if 'org-' in authority:
                # Extract org ID from subdomain like org-12345.localhost:4317
                try:
                    # Find the part that starts with 'org-'
                    parts = authority.split('.')
                    for part in parts:
                        if part.startswith('org-'):
                            organization_id = part
                            break
                except (IndexError, ValueError):
                    pass
        
        return organization_id
    
    async def get_organization_from_token(self, context) -> Optional[str]:
        """Extract organization ID from Bearer token in gRPC metadata"""
        metadata = dict(context.invocation_metadata())
        authorization = metadata.get('authorization')
        
        if not authorization or not authorization.startswith('Bearer '):
            return None
        
        token = authorization[7:]  # Remove 'Bearer ' prefix
        
        # Use the centralized auth function
        from .auth import get_org_id_from_token
        return await get_org_id_from_token(token)
    
    async def start(self):
        """Start the OTLP gRPC server"""
        self.server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
        
        # Add a generic service that routes to organization-specific services
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
            OrganizationRouterTraceService(self), self.server
        )
        metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(
            OrganizationRouterMetricsService(self), self.server
        )
        logs_service_pb2_grpc.add_LogsServiceServicer_to_server(
            OrganizationRouterLogsService(self), self.server
        )
        
        listen_addr = f'[::]:{self.port}'
        self.server.add_insecure_port(listen_addr)
        
        logger.info(f"Starting OTLP gRPC server on {listen_addr}")
        await self.server.start()
        logger.info("OTLP gRPC server started successfully")
    
    async def stop(self):
        """Stop the OTLP gRPC server"""
        if self.server:
            logger.info("Stopping OTLP gRPC server...")
            await self.server.stop(grace=5.0)
            logger.info("OTLP gRPC server stopped")
    
    async def wait_for_termination(self):
        """Wait for server termination"""
        if self.server:
            await self.server.wait_for_termination()

class OrganizationRouterTraceService(trace_service_pb2_grpc.TraceServiceServicer):
    """Router service that routes trace requests to organization-specific services"""
    
    def __init__(self, otlp_server: OTLPServer):
        self.otlp_server = otlp_server
    
    async def Export(self, request, context):
        """Route trace export to organization-specific service"""
        # Try to get organization ID from token first, then metadata
        organization_id = await self.otlp_server.get_organization_from_token(context)
        
        if not organization_id:
            organization_id = self.otlp_server.get_organization_from_metadata(context)
        
        if not organization_id:
            context.set_code(grpc.StatusCode.UNAUTHENTICATED)
            context.set_details("Organization ID required in Bearer token, metadata, or subdomain")
            return None
        
        if organization_id not in self.otlp_server.organization_services:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Organization {organization_id} not found or OTLP not enabled")
            return None
        
        # Route to organization-specific service
        service = self.otlp_server.organization_services[organization_id]['trace_service']
        return await service.Export(request, context)

class OrganizationRouterMetricsService(metrics_service_pb2_grpc.MetricsServiceServicer):
    """Router service that routes metrics requests to organization-specific services"""
    
    def __init__(self, otlp_server: OTLPServer):
        self.otlp_server = otlp_server
    
    async def Export(self, request, context):
        """Route metrics export to organization-specific service"""
        # Try to get organization ID from token first, then metadata
        organization_id = await self.otlp_server.get_organization_from_token(context)
        
        if not organization_id:
            organization_id = self.otlp_server.get_organization_from_metadata(context)
        
        if not organization_id:
            context.set_code(grpc.StatusCode.UNAUTHENTICATED)
            context.set_details("Organization ID required in Bearer token, metadata, or subdomain")
            return None
        
        if organization_id not in self.otlp_server.organization_services:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Organization {organization_id} not found or OTLP not enabled")
            return None
        
        # Route to organization-specific service
        service = self.otlp_server.organization_services[organization_id]['metrics_service']
        return await service.Export(request, context)

class OrganizationRouterLogsService(logs_service_pb2_grpc.LogsServiceServicer):
    """Router service that routes logs requests to organization-specific services"""
    
    def __init__(self, otlp_server: OTLPServer):
        self.otlp_server = otlp_server
    
    async def Export(self, request, context):
        """Route logs export to organization-specific service"""
        # Try to get organization ID from token first, then metadata
        organization_id = await self.otlp_server.get_organization_from_token(context)
        
        if not organization_id:
            organization_id = self.otlp_server.get_organization_from_metadata(context)
        
        if not organization_id:
            context.set_code(grpc.StatusCode.UNAUTHENTICATED)
            context.set_details("Organization ID required in Bearer token, metadata, or subdomain")
            return None
        
        if organization_id not in self.otlp_server.organization_services:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Organization {organization_id} not found or OTLP not enabled")
            return None
        
        # Route to organization-specific service
        service = self.otlp_server.organization_services[organization_id]['logs_service']
        return await service.Export(request, context)

# Global OTLP server instance
otlp_server = OTLPServer()

async def start_otlp_server():
    """Start the OTLP server"""
    await otlp_server.start()

async def stop_otlp_server():
    """Stop the OTLP server"""
    await otlp_server.stop()

def add_organization_to_otlp(organization_id: str):
    """Add an organization to the OTLP server"""
    otlp_server.add_organization_services(organization_id)

def remove_organization_from_otlp(organization_id: str):
    """Remove an organization from the OTLP server"""
    otlp_server.remove_organization_services(organization_id)

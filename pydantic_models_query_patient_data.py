"""
Pydantic models for aps_query_patient_data Lambda function.
Provides structured validation for query responses.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Literal
from enum import Enum


# Query Type Enum
class QueryType(str, Enum):
    """Supported query types for patient data"""
    FAMILY_HISTORY = "family_history"
    PATIENT_INFO = "patient_info"
    MEDICATIONS = "medications"
    CONDITIONS = "conditions"
    LAB_RESULTS = "lab_results"
    VITALS_TREND = "vitals_trend"
    SUMMARY = "summary"


# Base Response Model
class QueryResponse(BaseModel):
    """Base response structure for all query types"""
    status: Literal["success", "error", "no_data"] = "success"
    answer: str = ""  # Human-readable answer for agent consumption


# Family History Models
class FamilyHistoryEntry(BaseModel):
    """Single family history entry with anti-hallucination validation"""
    relation: str = ""
    condition: str = ""
    diagnosis_age: str = ""
    notes: str = ""
    status: Optional[str] = None  # relation_unspecified, valid
    safe_response: Optional[str] = None  # Pre-validated answer
    warning: Optional[str] = None


class FamilyHistoryResponse(QueryResponse):
    """Response for family_history query"""
    family_history: List[FamilyHistoryEntry] = Field(default_factory=list)
    total_entries: int = 0


# Patient Info Models
class CurrentVitals(BaseModel):
    """Current vital signs"""
    bmi: str = ""
    bp: str = ""
    heart_rate: str = ""
    weight: str = ""
    height: str = ""


class PatientInfoResponse(QueryResponse):
    """Response for patient_info query"""
    patient_name: str = ""
    age: str = ""
    sex: str = ""
    date_of_birth: str = ""
    pcp: str = ""
    insurance_provider: str = ""
    current_vitals: CurrentVitals = Field(default_factory=CurrentVitals)
    risk_level: str = ""


# Medications Models
class Medication(BaseModel):
    """Single medication entry"""
    name: str = ""
    category: str = ""  # HIGH RISK, Standard
    rxnorm_code: str = ""
    snomed_code: str = ""
    description: str = ""


class MedicationsResponse(QueryResponse):
    """Response for medications query"""
    medications: List[Medication] = Field(default_factory=list)
    total_count: int = 0


# Conditions Models
class Condition(BaseModel):
    """Single medical condition entry"""
    name: str = ""
    category: str = ""  # high_risk, chronic, other
    snomed_code: str = ""
    icd10_code: str = ""
    description: str = ""


class ConditionsResponse(QueryResponse):
    """Response for conditions query"""
    risk_level: str = ""
    key_high_risk_conditions: List[Condition] = Field(default_factory=list)
    chronic_conditions: List[Condition] = Field(default_factory=list)
    other_conditions: List[Condition] = Field(default_factory=list)


# Lab Results Models
class LabResult(BaseModel):
    """Single lab result entry"""
    test_name: str = ""
    result: str = ""  # Value field from JSON
    date: str = ""
    normal_range: str = ""
    status: str = ""


class LabResultsResponse(QueryResponse):
    """Response for lab_results query"""
    lab_results: List[LabResult] = Field(default_factory=list)
    total_count: int = 0


# Vitals Trend Models
class VitalsTrendData(BaseModel):
    """Time-series vitals data"""
    dates: List[str] = Field(default_factory=list)
    bp: List[str] = Field(default_factory=list)
    heart_rate: List[str] = Field(default_factory=list)
    weight: List[str] = Field(default_factory=list)


class VitalsTrendResponse(QueryResponse):
    """Response for vitals_trend query"""
    vitals_trend: VitalsTrendData = Field(default_factory=VitalsTrendData)
    vitals_summary: str = ""
    vitals_risk_level: str = ""


# Summary Models
class SummaryResponse(QueryResponse):
    """Response for summary query"""
    patient_name: str = ""
    age: str = ""
    risk_level: str = ""
    narrative_summary: str = ""
    key_summary_points: List[str] = Field(default_factory=list)
    key_high_risk_conditions: List[str] = Field(default_factory=list)
    overall_risk_assessment: str = ""


# Lambda Event Models (for Bedrock Agent integration)
class Parameter(BaseModel):
    """Single parameter from Bedrock Agent"""
    name: str
    type: str = "string"
    value: str


class RequestBodyProperty(BaseModel):
    """Request body property from Bedrock Agent"""
    name: str
    type: str = "string"
    value: str


class RequestBodyContent(BaseModel):
    """Request body content structure"""
    properties: List[RequestBodyProperty] = Field(default_factory=list)


class RequestBody(BaseModel):
    """Request body from Bedrock Agent"""
    content: Dict[str, RequestBodyContent] = Field(default_factory=dict)


class LambdaEvent(BaseModel):
    """Complete Lambda event from Bedrock Agent"""
    actionGroup: str
    apiPath: str
    httpMethod: str = "POST"
    parameters: List[Parameter] = Field(default_factory=list)
    requestBody: Optional[RequestBody] = None


# Response wrapper for Lambda
class LambdaResponse(BaseModel):
    """Lambda response structure for Bedrock Agent"""
    messageVersion: str = "1.0"
    response: Dict = Field(default_factory=dict)
    
    @classmethod
    def success(cls, action_group: str, api_path: str, response_body: Dict):
        """Create successful Lambda response"""
        return cls(
            messageVersion="1.0",
            response={
                "actionGroup": action_group,
                "apiPath": api_path,
                "httpMethod": "POST",
                "httpStatusCode": 200,
                "responseBody": {
                    "application/json": {
                        "body": response_body
                    }
                }
            }
        )
    
    @classmethod
    def error(cls, action_group: str, api_path: str, error_message: str):
        """Create error Lambda response"""
        return cls(
            messageVersion="1.0",
            response={
                "actionGroup": action_group,
                "apiPath": api_path,
                "httpMethod": "POST",
                "httpStatusCode": 500,
                "responseBody": {
                    "application/json": {
                        "body": {
                            "status": "error",
                            "answer": error_message
                        }
                    }
                }
            }
        )

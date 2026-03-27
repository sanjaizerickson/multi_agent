"""
APS Query Patient Data Lambda Function

PURPOSE:
This Lambda function provides structured query access to patient medical data
stored in S3, with built-in anti-hallucination validation (especially for family history).

INTEGRATION:
- AWS Bedrock Agent action group: query-patient-data
- S3 bucket: aps-summarization-poc
- Pydantic models: pydantic_models_query_patient_data.py (MUST be uploaded as layer or included)

DEPLOYMENT NOTES:
1. Upload BOTH files to Lambda:
   - aps_query_patient_data.py (this file)
   - pydantic_models_query_patient_data.py

2. Handler: aps_query_patient_data.lambda_handler

3. Runtime: Python 3.11+

4. Required IAM permissions:
   - s3:GetObject on aps-summarization-poc bucket

5. Memory: 512MB, Timeout: 30s

QUERY TYPES:
- family_history: Anti-hallucination validated family history
- patient_info: Demographics + current vitals
- medications: Medication list (high-risk flagged)
- conditions: Conditions by risk level
- lab_results: Recent lab results
- vitals_trend: Vitals over time
- summary: Executive summary
"""

import json
import boto3
import os
import re
from datetime import datetime
from botocore.config import Config

# Import Pydantic models for structured validation
from pydantic_models_query_patient_data import (
    FamilyHistoryEntry,
    FamilyHistoryResponse,
    PatientInfoResponse,
    CurrentVitals,
    Medication,
    MedicationsResponse,
    Condition,
    ConditionsResponse,
    LabResult,
    LabResultsResponse,
    VitalsTrendData,
    VitalsTrendResponse,
    SummaryResponse,
    LambdaResponse,
    QueryType
)

# Load configuration from config.local.json (same pattern as other Lambda scripts)
def load_config():
    """Load configuration from config.local.json if it exists"""
    config_path = os.path.join(os.path.dirname(__file__), "config.local.json")
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("[CONFIG] config.local.json not found - using environment variables")
        return None
    except Exception as e:
        print(f"[CONFIG ERROR] Failed to load config: {e}")
        return None

# Initialize AWS clients with profile from config (same pattern as aps_risk_analyzer_and_summary.py)
try:
    config = load_config()
    
    if config:
        # Use config file settings - support both flat and nested formats
        # Flat format: {"aws_profile": "default", "s3_bucket": "...", ...}
        # Nested format: {"aws": {"profile": "default", ...}}
        AWS_PROFILE = config.get("aws_profile") or config.get("aws", {}).get("profile", "default")
        AWS_REGION = config.get("aws_region") or config.get("aws", {}).get("region", "us-east-1")
        # Support both s3_bucket (current) and bucket_name (legacy) field names
        BUCKET_NAME = config.get("s3_bucket") or config.get("bucket_name") or config.get("aws", {}).get("bucket_name", "aps-summarization-poc")
        
        print(f"[CONFIG] Loading AWS profile: {AWS_PROFILE}, region: {AWS_REGION}, bucket: {BUCKET_NAME}")
        
        # Configure boto3 with timeouts to prevent hanging
        # Same pattern as aps_risk_analyzer_and_summary.py
        boto_config = Config(
            region_name=AWS_REGION,
            connect_timeout=30,          # Connection timeout
            read_timeout=120,            # Read timeout for large S3 files
            retries={'max_attempts': 3, 'mode': 'standard'},
            max_pool_connections=10      # Connection pool size
        )
        
        # Create a session with the specified profile
        boto_session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        
        # Create S3 client using the session with timeout config
        s3_client = boto_session.client("s3", config=boto_config)
        
        print(f"[CONFIG] AWS clients initialized successfully with profile '{AWS_PROFILE}'")
        print("[CONFIG] Connection timeout: 30s, Read timeout: 120s, Pool size: 10")
    else:
        # Use default AWS credentials (environment variables, IAM role, etc.)
        AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
        BUCKET_NAME = os.environ.get('BUCKET_NAME', 'aps-summarization-poc')
        
        print("[CONFIG] Using default AWS credentials from environment")
        
        # Configure boto3 with timeouts to prevent hanging
        boto_config = Config(
            region_name=AWS_REGION,
            connect_timeout=30,
            read_timeout=120,
            retries={'max_attempts': 3, 'mode': 'standard'},
            max_pool_connections=10
        )
        
        s3_client = boto3.client("s3", config=boto_config)
        
        print(f"[CONFIG] AWS clients initialized with region: {AWS_REGION}")
        print("[CONFIG] Connection timeout: 30s, Read timeout: 120s, Pool size: 10")

except Exception as e:
    print(f"[CONFIG ERROR] Failed to initialize AWS clients: {e}")
    print("[CONFIG ERROR] Script may fail during S3 operations")
    # Create clients anyway with fallback (will fail at runtime if credentials missing)
    AWS_REGION = "us-east-1"
    BUCKET_NAME = "aps-summarization-poc"
    boto_config = Config(
        region_name=AWS_REGION,
        connect_timeout=30,
        read_timeout=120,
        retries={'max_attempts': 3, 'mode': 'standard'}
    )
    s3_client = boto3.client("s3", config=boto_config)


def load_patient_data_from_s3(session_id):
    """
    Load both medical_summary.json and enhanced_medical_summary_with_risks.json from S3.
    Returns combined data structure for comprehensive queries.
    """
    print(f"[S3] Loading patient data for session: {session_id}")
    
    try:
        # Load enhanced summary (primary source)
        enhanced_key = f"{session_id}/outputs/enhanced_medical_summary_with_risks.json"
        enhanced_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=enhanced_key)
        enhanced_data = json.loads(enhanced_response['Body'].read().decode('utf-8'))
        print(f"[S3] Loaded enhanced_medical_summary_with_risks.json")
        
        # Load medical summary (for detailed encounter/vitals data)
        medical_key = f"{session_id}/outputs/medical_summary.json"
        try:
            medical_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=medical_key)
            medical_data = json.loads(medical_response['Body'].read().decode('utf-8'))
            print(f"[S3] Loaded medical_summary.json")
        except s3_client.exceptions.NoSuchKey:
            print(f"[S3] medical_summary.json not found - using enhanced data only")
            medical_data = None
        
        # Combine data (enhanced is primary, medical provides supplementary details)
        combined_data = {
            'enhanced': enhanced_data,
            'medical': medical_data.get('summary', {}) if medical_data else {}
        }
        
        return combined_data
        
    except s3_client.exceptions.NoSuchKey as e:
        print(f"[S3 ERROR] Data not found for session {session_id}: {e}")
        # Return structured response instead of exception (enables clean orchestration)
        return {
            "status": "no_data",
            "answer": "Document has not been analyzed yet. Please analyze the document first before asking questions about patient data."
        }
    except Exception as e:
        print(f"[S3 ERROR] Failed to load data: {e}")
        return {
            "status": "error",
            "answer": f"Error loading patient data: {str(e)}"
        }


def get_family_history(data):
    """
    CRITICAL ANTI-HALLUCINATION HANDLER for family history.
    Pre-validates relation field and returns safe, formatted response.
    This prevents the agent from fabricating family members.
    """
    print("[HANDLER] Processing family_history query")
    
    # Try enhanced data first, fallback to medical summary
    family_history = data['enhanced'].get('family_history', [])
    if not family_history and data['medical']:
        family_history = data['medical'].get('family_history', [])
    
    if not family_history:
        return {
            'status': 'no_data',
            'answer': 'No family history documented in available medical records.',
            'family_history': []
        }
    
    # Process each family history entry with strict validation
    validated_entries = []
    
    for entry in family_history:
        relation = entry.get('relation', 'Not specified')
        condition = entry.get('condition', 'Unknown')
        diagnosis_age = entry.get('diagnosis_age', '')
        notes = entry.get('notes', '')
        
        # CRITICAL CHECK: Prevent hallucination for unspecified relations
        if relation in ['Not specified', 'Unknown', '', None]:
            # Return pre-formatted safe response
            validated_entries.append({
                'status': 'relation_unspecified',
                'safe_response': f"Family history of {condition} is documented, but the specific family member (father, mother, sibling, grandparent) is not specified in the available medical records.",
                'condition': condition,
                'notes': notes or '',
                'warning': 'DO NOT fabricate specific family members - relation not documented'
            })
        else:
            # Relation is specified - safe to return detailed info
            response_text = f"{relation} has {condition}"
            if diagnosis_age and diagnosis_age not in ['Not specified', 'Unknown', '']:
                response_text += f", diagnosed at age {diagnosis_age}"
            else:
                response_text += " (age at diagnosis not documented)"
            
            validated_entries.append({
                'status': 'relation_specified',
                'relation': relation,
                'condition': condition,
                'diagnosis_age': diagnosis_age if diagnosis_age else 'Not documented',
                'safe_response': response_text,
                'notes': notes or ''
            })
    
    # Return standardized answer field for clean agent consumption
    if validated_entries and validated_entries[0].get('status') == 'relation_unspecified':
        answer = validated_entries[0]['safe_response']
    elif validated_entries:
        # Format documented relations (status is 'relation_specified')
        answer_parts = []
        for entry in validated_entries:
            if entry.get('status') == 'relation_specified':
                answer_parts.append(entry.get('safe_response', f"{entry['relation']} has {entry['condition']}"))
        answer = ". ".join(answer_parts) if answer_parts else "Family history documented."
    else:
        answer = "No family history documented."
    
    # Convert to Pydantic models — coerce None to "" for all str fields to avoid ValidationError
    family_history_models = [
        FamilyHistoryEntry(
            relation=e.get('relation') or 'Not specified',
            condition=e.get('condition') or 'Unknown',
            diagnosis_age=e.get('diagnosis_age') or '',
            notes=e.get('notes') or '',
            status=e.get('status'),
            safe_response=e.get('safe_response'),
            warning=e.get('warning')
        ) for e in validated_entries
    ]
    
    return FamilyHistoryResponse(
        status='success',
        answer=answer,
        family_history=family_history_models,
        total_entries=len(validated_entries)
    )


def get_patient_info(data):
    """Get basic patient demographics and current health status"""
    print("[HANDLER] Processing patient_info query")
    
    # Get from executive summary (enhanced data)
    exec_summary = data['enhanced'].get('executive_summary', {})
    patient_demographics = exec_summary.get('patient_demographics', {})
    health_latest = exec_summary.get('health_latest', {})
    
    # Fallback to medical summary if enhanced doesn't have it
    if not patient_demographics and data['medical']:
        patient_demographics = data['medical'].get('overview', {})
        health_latest = data['medical'].get('health_latest', {})
    
    name = patient_demographics.get('name', 'Not documented')
    age = patient_demographics.get('age', 'Not documented')
    sex = patient_demographics.get('sex', 'Not documented')
    
    # Build answer string
    answer = f"Patient: {name}, Age: {age}, Sex: {sex}"
    
    # Build Pydantic models
    vitals = CurrentVitals(
        bmi=health_latest.get('bmi', 'Not documented'),
        bp=health_latest.get('bp', 'Not documented'),
        heart_rate=health_latest.get('heart_rate', 'Not documented'),
        weight=health_latest.get('weight', 'Not documented'),
        height=health_latest.get('height', 'Not documented')
    )
    
    return PatientInfoResponse(
        status='success',
        answer=answer,
        patient_name=name,
        age=age,
        sex=sex,
        date_of_birth=patient_demographics.get('date_of_birth', 'Not documented'),
        pcp=patient_demographics.get('pcp', 'Not documented'),
        insurance_provider=patient_demographics.get('insurance_provider', 'Not documented'),
        current_vitals=vitals,
        risk_level=exec_summary.get('risk_level', 'Not assessed')
    )


def get_medications(data):
    """
    Get medication list (limited to top 10 to avoid token limits).
    Returns structured medication data with dosages.
    """
    print("[HANDLER] Processing medications query")
    
    # Try to get structured medications from enhanced data
    high_risk_meds = data['enhanced'].get('high_risk_medications', [])
    other_meds = data['enhanced'].get('other_medications', [])
    
    all_medications = []
    
    # Add high-risk medications first (marked)
    for med in high_risk_meds[:5]:  # Limit to 5 high-risk
        all_medications.append({
            'name': med.get('condition') or med.get('medication') or med.get('medication_name', 'Unknown'),
            'category': 'HIGH RISK',
            'rxnorm_code': med.get('rxnorm_code', ''),
            'snomed_code': med.get('snomed_code', ''),
            'description': med.get('description', '')[:200] if med.get('description') else ''
        })
    
    # Add other medications
    for med in other_meds[:5]:  # Limit to 5 other
        all_medications.append({
            'name': med.get('condition') or med.get('medication') or med.get('medication_name', 'Unknown'),
            'category': 'Standard',
            'rxnorm_code': med.get('rxnorm_code', ''),
            'snomed_code': med.get('snomed_code', ''),
            'description': med.get('description', '')[:200] if med.get('description') else ''
        })
    
    # Fallback: If no structured data, parse medication_history narrative
    if not all_medications:
        medication_history = data['enhanced'].get('medication_history') or \
                           (data['medical'].get('medication_history') if data['medical'] else None)
        
        if medication_history:
            # Parse medication names from narrative (comma-separated list)
            # Example: "Lisinopril 10mg daily, Atorvastatin 20mg nightly, Famotidine PRN"
            
            # Split by commas and extract medication names (first word before dose)
            med_entries = medication_history.split(',')
            for entry in med_entries[:10]:  # Limit to 10 medications
                entry = entry.strip()
                # Extract first word (medication name) before dosage pattern
                match = re.match(r'^([A-Za-z-]+)', entry)
                if match:
                    med_name = match.group(1)
                    all_medications.append({
                        'name': med_name,
                        'category': 'Standard',
                        'snomed_code': '',
                        'rxnorm_code': '',
                        'description': entry[:100]  # Full entry as description
                    })
    
    # Build answer string
    if all_medications:
        answer = f"Patient is taking {len(all_medications)} medications: " + ", ".join([m['name'] for m in all_medications[:5]])
    else:
        answer = "No medications documented."
    
    # Convert to Pydantic models
    medication_models = [
        Medication(
            name=m['name'],
            category=m['category'],
            rxnorm_code=m['rxnorm_code'],
            snomed_code=m['snomed_code'],
            description=m['description']
        ) for m in all_medications
    ]
    
    return MedicationsResponse(
        status='success',
        answer=answer,
        medications=medication_models,
        total_count=len(high_risk_meds) + len(other_meds)
    )


def get_conditions(data):
    """
    Get medical conditions categorized by risk level.
    Returns top conditions to avoid token limits.
    """
    print("[HANDLER] Processing conditions query")
    
    high_risk = data['enhanced'].get('high_risk_conditions', [])[:3]
    chronic = data['enhanced'].get('chronic_conditions', [])[:5]
    other = data['enhanced'].get('other_conditions', [])[:5]
    
    risk_level = data['enhanced'].get('executive_summary', {}).get('risk_level', 'Not assessed')
    
    result = {
        'status': 'success',
        'risk_level': risk_level,
        'key_high_risk_conditions': []
    }
    
    # Format high-risk conditions
    for cond in high_risk:
        result['key_high_risk_conditions'].append({
            'name': cond.get('condition') or cond.get('condition_name', 'Unknown'),
            'category': 'HIGH RISK',
            'snomed_code': cond.get('snomed_code', ''),
            'description': cond.get('description', '')[:200] if cond.get('description') else ''
        })
    
    # Format chronic conditions
    result['chronic_conditions'] = []
    for cond in chronic:
        result['chronic_conditions'].append({
            'name': cond.get('condition') or cond.get('condition_name', 'Unknown'),
            'category': 'CHRONIC',
            'snomed_code': cond.get('snomed_code', ''),
            'description': cond.get('description', '')[:200] if cond.get('description') else ''
        })
    
    # Format other conditions (limited)
    result['other_conditions'] = []
    for cond in other:
        result['other_conditions'].append({
            'name': cond.get('condition') or cond.get('condition_name', 'Unknown'),
            'category': 'Other',
            'snomed_code': cond.get('snomed_code', ''),
            'description': cond.get('description', '')[:150] if cond.get('description') else ''
        })
    
    # Build answer string
    answer_parts = [f"Risk Level: {risk_level}"]
    if result['key_high_risk_conditions']:
        answer_parts.append(f"High-risk conditions: {', '.join([c['name'] for c in result['key_high_risk_conditions']])}")
    if result['chronic_conditions']:
        answer_parts.append(f"Chronic conditions: {', '.join([c['name'] for c in result['chronic_conditions'][:3]])}")
    
    answer = ". ".join(answer_parts)
    
    # Convert to Pydantic models
    high_risk_models = [Condition(**c) for c in result['key_high_risk_conditions']]
    chronic_models = [Condition(**c) for c in result['chronic_conditions']]
    other_models = [Condition(**c) for c in result['other_conditions']]
    
    return ConditionsResponse(
        status='success',
        answer=answer,
        risk_level=risk_level,
        key_high_risk_conditions=high_risk_models,
        chronic_conditions=chronic_models,
        other_conditions=other_models
    )


def get_lab_results(data):
    """
    Get recent lab results (limited to last 10 to avoid token limits).
    Sorted by date, most recent first.
    """
    print("[HANDLER] Processing lab_results query")
    
    # Try medical summary first (has more detailed lab data)
    labs = []
    if data['medical']:
        labs = data['medical'].get('lab_results', [])
    
    # Fallback to enhanced data
    if not labs:
        labs = data['enhanced'].get('lab_results', [])
    
    if not labs:
        return {
            'status': 'no_data',
            'answer': 'No lab results documented in available medical records.',
            'lab_results': []
        }
    
    # Sort by date (most recent first) and limit to 10
    try:
        sorted_labs = sorted(
            labs,
            key=lambda x: x.get('date', '1900-01-01'),
            reverse=True
        )[:10]
    except:
        sorted_labs = labs[:10]
    
    # Format lab results
    formatted_labs = []
    for lab in sorted_labs:
        formatted_labs.append({
            'test_name': lab.get('test_name', 'Unknown'),
            'result': lab.get('value') or lab.get('result', 'Not available'),  # Try 'value' first, then 'result'
            'normal_range': lab.get('reference_range') or lab.get('normal_range', ''),
            'status': lab.get('status', 'Unknown'),
            'date': lab.get('date', 'Not documented')
        })
    
    # Build answer string
    if formatted_labs:
        answer = f"Found {len(labs)} lab results. Most recent: " + ", ".join([f"{lab['test_name']}: {lab['result']}" for lab in formatted_labs[:3]])
    else:
        answer = "No lab results available."
    
    # Convert to Pydantic models
    lab_models = [LabResult(**lab) for lab in formatted_labs]
    
    return LabResultsResponse(
        status='success',
        answer=answer,
        lab_results=lab_models,
        total_count=len(labs)
    )


def get_summary(data):
    """
    Get executive summary with risk assessment.
    Returns high-level overview for conversational context.
    """
    print("[HANDLER] Processing summary query")
    
    exec_summary = data['enhanced'].get('executive_summary', {})
    
    narrative = exec_summary.get('narrative_summary', 'No summary available')[:500]
    
    # Convert list of dicts to list of strings for key_high_risk_conditions
    high_risk_conditions = exec_summary.get('key_high_risk_conditions', [])[:5]
    if high_risk_conditions and isinstance(high_risk_conditions[0], dict):
        high_risk_conditions = [c.get('name', str(c)) for c in high_risk_conditions]
    
    return SummaryResponse(
        status='success',
        answer=narrative,
        patient_name=exec_summary.get('patient_demographics', {}).get('name', 'Not documented'),
        age=exec_summary.get('patient_demographics', {}).get('age', 'Not documented'),
        risk_level=exec_summary.get('risk_level', 'Not assessed'),
        narrative_summary=narrative,
        key_summary_points=exec_summary.get('key_summary_points', [])[:5],
        key_high_risk_conditions=high_risk_conditions,
        overall_risk_assessment=exec_summary.get('overall_risk_assessment', 'Not assessed')[:400]
    )


def get_vitals_trend(data):
    """Get vital signs trends over time"""
    print("[HANDLER] Processing vitals_trend query")
    
    vitals_trend = None
    if data['medical']:
        vitals_trend = data['medical'].get('vitals_trend', {})
    
    if not vitals_trend or not vitals_trend.get('dates'):
        return {
            'status': 'no_data',
            'answer': 'No vitals trend data available in the medical records.',
            'vitals_trend': {}
        }
    
    # Get vitals summary
    vitals_summary = data['medical'].get('vitals_summary', 'No vitals summary available')
    vitals_risk = data['medical'].get('vitals_risk_level', 'Not assessed')
    
    # Use vitals summary as answer
    answer = vitals_summary[:400] if vitals_summary else "Vitals trend data available."
    
    # Build Pydantic models
    trend_data = VitalsTrendData(
        dates=vitals_trend.get('dates', []),
        bp=vitals_trend.get('bp', []),
        heart_rate=vitals_trend.get('heart_rate', []),
        weight=vitals_trend.get('weight', [])
    )
    
    return VitalsTrendResponse(
        status='success',
        answer=answer,
        vitals_trend=trend_data,
        vitals_summary=vitals_summary[:400],
        vitals_risk_level=vitals_risk
    )


def lambda_handler(event, context):
    """
    AWS Lambda handler for querying patient medical data.
    
    Expected event format from Bedrock Agent:
    {
        "actionGroup": "query-patient-data",
        "apiPath": "/query-patient-data",
        "httpMethod": "POST",
        "parameters": [
            {"name": "sessionId", "value": "abc-123"},
            {"name": "queryType", "value": "family_history"}
        ],
        "requestBody": {
            "content": {
                "application/json": {
                    "properties": [
                        {"name": "sessionId", "value": "abc-123"},
                        {"name": "queryType", "value": "family_history"}
                    ]
                }
            }
        }
    }
    
    Query types:
    - family_history: Get family history with anti-hallucination validation
    - patient_info: Get patient demographics and current vitals
    - medications: Get medication list
    - conditions: Get medical conditions by risk category
    - lab_results: Get recent lab results
    - summary: Get executive summary
    - vitals_trend: Get vitals over time
    """
    
    print(f"[LAMBDA] Invoked at {datetime.utcnow().isoformat()}")
    print(f"[EVENT] {json.dumps(event, indent=2)}")
    
    try:
        # Extract parameters from Bedrock Agent event format
        session_id = None
        query_type = "summary"  # Default
        question = None  # Original user question (for future use/debugging)
        
        # Try to get from parameters array (Bedrock Agent format)
        if 'parameters' in event:
            for param in event['parameters']:
                if param.get('name') == 'sessionId':
                    session_id = param.get('value')
                elif param.get('name') == 'queryType':
                    query_type = param.get('value')
                elif param.get('name') == 'question':
                    question = param.get('value')
        
        # Fallback: Try to get from requestBody
        if not session_id and 'requestBody' in event:
            body_content = event['requestBody'].get('content', {}).get('application/json', {})
            properties = body_content.get('properties', [])
            for prop in properties:
                if prop.get('name') == 'sessionId':
                    session_id = prop.get('value')
                elif prop.get('name') == 'queryType':
                    query_type = prop.get('value')
        
        # Validation
        if not session_id:
            return {
                'messageVersion': '1.0',
                'response': {
                    'actionGroup': event.get('actionGroup', 'query-patient-data'),
                    'apiPath': event.get('apiPath', '/query-patient-data'),
                    'httpMethod': 'POST',
                    'httpStatusCode': 400,
                    'responseBody': {
                        'application/json': {
                            'body': json.dumps({
                                'status': 'error',
                                'answer': 'Missing required parameter: sessionId. Please provide the session ID.'
                            })
                        }
                    }
                }
            }
        
        print(f"[PARAMS] sessionId={session_id}, queryType={query_type}, question={question}")
        
        # Load patient data from S3
        data = load_patient_data_from_s3(session_id)
        
        # Handle no_data response from load function
        if isinstance(data, dict) and data.get('status') in ['no_data', 'error']:
            # This is already a dict, use it as-is
            result_dict = data
        else:
            # Route to appropriate handler (returns Pydantic model)
            result = None
            if query_type == 'family_history':
                result = get_family_history(data)
            elif query_type == 'patient_info':
                result = get_patient_info(data)
            elif query_type == 'medications':
                result = get_medications(data)
            elif query_type == 'conditions':
                result = get_conditions(data)
            elif query_type == 'lab_results':
                result = get_lab_results(data)
            elif query_type == 'vitals_trend':
                result = get_vitals_trend(data)
            elif query_type == 'summary':
                result = get_summary(data)
            else:
                # CRITICAL: Unknown queryType fallback
                print(f"[WARNING] Unknown queryType received: {query_type}")
                result = {
                    'status': 'unknown_query',
                    'answer': 'Unable to determine the requested data category. Please specify what medical information you need (e.g., family history, lab results, medications, conditions, patient info, vitals, or summary).',
                    'available_query_types': [
                        'family_history',
                        'patient_info',
                        'medications',
                        'conditions',
                        'lab_results',
                        'summary',
                        'vitals_trend'
                    ]
                }
            
            # Convert Pydantic model to dict (Pydantic models have .model_dump() method)
            if hasattr(result, 'model_dump'):
                result_dict = result.model_dump(exclude_none=True)
            else:
                result_dict = result
        
        # Log the answer field for debugging
        print(f"[RESPONSE] answer: {result_dict.get('answer', 'N/A')[:100]}...")
        
        # Return response in Bedrock Agent format
        response_body = {
            'application/json': {
                'body': json.dumps(result_dict)
            }
        }
        
        action_response = {
            'actionGroup': event.get('actionGroup', 'query-patient-data'),
            'apiPath': event.get('apiPath', '/query-patient-data'),
            'httpMethod': event.get('httpMethod', 'POST'),
            'httpStatusCode': 200,
            'responseBody': response_body
        }
        
        session_attributes = event.get('sessionAttributes', {})
        prompt_session_attributes = event.get('promptSessionAttributes', {})
        
        print(f"[SUCCESS] Returning {query_type} data")
        
        return {
            'messageVersion': '1.0',
            'response': action_response,
            'sessionAttributes': session_attributes,
            'promptSessionAttributes': prompt_session_attributes
        }
        
    except Exception as e:
        print(f"[ERROR] {str(e)}")
        
        error_body = {
            'application/json': {
                'body': json.dumps({
                    'status': 'error',
                    'answer': f'Failed to query patient data: {str(e)}'
                })
            }
        }
        
        return {
            'messageVersion': '1.0',
            'response': {
                'actionGroup': event.get('actionGroup', 'query-patient-data'),
                'apiPath': event.get('apiPath', '/query-patient-data'),
                'httpMethod': event.get('httpMethod', 'POST'),
                'httpStatusCode': 500,
                'responseBody': error_body
            }
        }


# For local testing
if __name__ == "__main__":
    """
    Local testing - Tests all 7 query types
    
    SETUP:
    1. Ensure config.local.json has correct AWS profile
    2. Use a session_id that has outputs in S3:
       - {session_id}/outputs/enhanced_medical_summary_with_risks.json
       - {session_id}/outputs/medical_summary.json
    """
    
    # CHANGE THIS to a valid session_id with S3 outputs
    TEST_SESSION_ID = "45b5b995-9973-4764-ab05-56846671229e"
    
    query_types = [
        ("family_history", "Family History (Anti-Hallucination Test)"),
        ("patient_info", "Patient Demographics"),
        ("medications", "Medication List"),
        ("conditions", "Medical Conditions"),
        ("lab_results", "Laboratory Results"),
        ("vitals_trend", "Vitals Trend Over Time"),
        ("summary", "Executive Summary")
    ]
    
    print("\n" + "="*80)
    print("APS QUERY PATIENT DATA - LOCAL TESTING")
    print("="*80)
    print(f"Session ID: {TEST_SESSION_ID}")
    print(f"Testing {len(query_types)} query types")
    print("="*80)
    
    results = []
    
    for query_type, description in query_types:
        print(f"\n🧪 TEST: {description}")
        print("-" * 80)
        
        test_event = {
            "actionGroup": "query-patient-data",
            "apiPath": "/query-patient-data",
            "httpMethod": "POST",
            "parameters": [
                {"name": "sessionId", "value": TEST_SESSION_ID},
                {"name": "queryType", "value": query_type}
            ],
            "sessionAttributes": {},
            "promptSessionAttributes": {}
        }
        
        try:
            result = lambda_handler(test_event, None)
            
            # Extract response body
            response_body = result.get('response', {}).get('responseBody', {})
            body_json = response_body.get('application/json', {}).get('body', '{}')
            parsed_body = json.loads(body_json)
            
            status = parsed_body.get('status')
            answer = parsed_body.get('answer', 'N/A')
            
            print(f"✅ Status: {status}")
            print(f"📄 Answer: {answer[:200]}..." if len(answer) > 200 else f"📄 Answer: {answer}")
            
            # Show specific fields based on query type
            if query_type == 'family_history' and 'family_history' in parsed_body:
                entries = parsed_body['family_history']
                print(f"📊 Entries: {len(entries)}")
                for entry in entries[:2]:  # Show first 2
                    print(f"   - {entry.get('status')}: {entry.get('safe_response', '')[:100]}")
            
            elif query_type == 'medications' and 'medications' in parsed_body:
                meds = parsed_body['medications']
                print(f"📊 Found: {len(meds)} medications")
            
            elif query_type == 'lab_results' and 'lab_results' in parsed_body:
                labs = parsed_body['lab_results']
                print(f"📊 Found: {len(labs)} lab results")
            
            results.append((description, True))
            
        except Exception as e:
            print(f"❌ FAILED: {str(e)}")
            results.append((description, False))
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    for description, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {description}")
    
    total_passed = sum(1 for _, success in results if success)
    print(f"\n📊 Total: {total_passed}/{len(results)} tests passed")
    
    if total_passed == len(results):
        print("\n🎉 All tests passed! Lambda function is ready for deployment.")
    else:
        print("\n⚠️ Some tests failed. Review errors above.")

import json
import boto3
import os
from datetime import datetime

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

# Initialize AWS clients with profile from config (same pattern as aps_medical_summary_generator.py)
try:
    config = load_config()
    
    if config:
        AWS_PROFILE = config.get('aws', {}).get('profile', 'default')
        AWS_REGION = config.get('aws', {}).get('region', 'us-east-1')
        BUCKET_NAME = config.get('aws', {}).get('bucket_name', 'aps-summarization-poc')
        
        print(f"[CONFIG] Using AWS profile: {AWS_PROFILE}, region: {AWS_REGION}, bucket: {BUCKET_NAME}")
        
        # Use profile for local development
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        s3_client = session.client('s3')
    else:
        # Use environment variables (Lambda execution role)
        AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
        BUCKET_NAME = os.environ.get('BUCKET_NAME', 'aps-summarization-poc')
        
        print(f"[CONFIG] Using environment variables: region={AWS_REGION}, bucket={BUCKET_NAME}")
        
        s3_client = boto3.client('s3', region_name=AWS_REGION)

except Exception as e:
    print(f"[CONFIG ERROR] Failed to initialize AWS clients: {e}")
    # Fallback configuration
    AWS_REGION = "us-east-1"
    BUCKET_NAME = "aps-summarization-poc"
    s3_client = boto3.client('s3', region_name=AWS_REGION)


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
        raise Exception(f"No analysis data found for session {session_id}. Please analyze a document first.")
    except Exception as e:
        print(f"[S3 ERROR] Failed to load data: {e}")
        raise Exception(f"Error loading patient data: {str(e)}")


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
            'message': 'No family history documented in available medical records.',
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
                'notes': notes if notes else None,
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
                'notes': notes if notes else None
            })
    
    return {
        'status': 'success',
        'message': f'Found {len(validated_entries)} family history entries',
        'family_history': validated_entries,
        'instruction': 'Use safe_response field exactly as provided - DO NOT modify or add details'
    }


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
    
    return {
        'status': 'success',
        'patient_name': patient_demographics.get('name', 'Not documented'),
        'age': patient_demographics.get('age', 'Not documented'),
        'sex': patient_demographics.get('sex', 'Not documented'),
        'date_of_birth': patient_demographics.get('date_of_birth', 'Not documented'),
        'pcp': patient_demographics.get('pcp', 'Not documented'),
        'insurance_provider': patient_demographics.get('insurance_provider', 'Not documented'),
        'current_vitals': {
            'bmi': health_latest.get('bmi', 'Not documented'),
            'bp': health_latest.get('bp', 'Not documented'),
            'heart_rate': health_latest.get('heart_rate', 'Not documented'),
            'weight': health_latest.get('weight', 'Not documented'),
            'height': health_latest.get('height', 'Not documented')
        },
        'risk_level': exec_summary.get('risk_level', 'Not assessed')
    }


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
            'name': med.get('medication_name', 'Unknown'),
            'category': 'HIGH RISK',
            'snomed_code': med.get('snomed_code', ''),
            'description': med.get('description', '')[:200] if med.get('description') else ''
        })
    
    # Add other medications
    for med in other_meds[:5]:  # Limit to 5 other
        all_medications.append({
            'name': med.get('medication_name', 'Unknown'),
            'category': 'Standard',
            'snomed_code': med.get('snomed_code', ''),
            'description': med.get('description', '')[:200] if med.get('description') else ''
        })
    
    # Fallback: If no structured data, use narrative
    if not all_medications:
        medication_history = data['enhanced'].get('medication_history') or \
                           (data['medical'].get('medication_history') if data['medical'] else None)
        
        if medication_history:
            return {
                'status': 'success',
                'message': 'Medication data available as narrative text',
                'narrative': medication_history[:500],  # Limit length
                'note': 'Full structured medication list not available - showing summary'
            }
    
    return {
        'status': 'success',
        'message': f'Found {len(all_medications)} medications (showing top 10)',
        'medications': all_medications,
        'total_count': len(high_risk_meds) + len(other_meds)
    }


def get_conditions(data):
    """
    Get medical conditions categorized by risk level.
    Returns top conditions to avoid token limits.
    """
    print("[HANDLER] Processing conditions query")
    
    high_risk = data['enhanced'].get('high_risk_conditions', [])[:3]
    chronic = data['enhanced'].get('chronic_conditions', [])[:5]
    other = data['enhanced'].get('other_conditions', [])[:5]
    
    result = {
        'status': 'success',
        'risk_level': data['enhanced'].get('executive_summary', {}).get('risk_level', 'Not assessed'),
        'key_high_risk_conditions': []
    }
    
    # Format high-risk conditions
    for cond in high_risk:
        result['key_high_risk_conditions'].append({
            'name': cond.get('condition_name', 'Unknown'),
            'category': 'HIGH RISK',
            'snomed_code': cond.get('snomed_code', ''),
            'description': cond.get('description', '')[:200] if cond.get('description') else ''
        })
    
    # Format chronic conditions
    result['chronic_conditions'] = []
    for cond in chronic:
        result['chronic_conditions'].append({
            'name': cond.get('condition_name', 'Unknown'),
            'category': 'CHRONIC',
            'snomed_code': cond.get('snomed_code', ''),
            'description': cond.get('description', '')[:200] if cond.get('description') else ''
        })
    
    # Format other conditions (limited)
    result['other_conditions'] = []
    for cond in other:
        result['other_conditions'].append({
            'name': cond.get('condition_name', 'Unknown'),
            'category': 'Other',
            'snomed_code': cond.get('snomed_code', ''),
            'description': cond.get('description', '')[:150] if cond.get('description') else ''
        })
    
    return result


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
            'message': 'No lab results documented',
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
            'result': lab.get('result', 'Not available'),
            'reference_range': lab.get('reference_range', ''),
            'status': lab.get('status', 'Unknown'),
            'date': lab.get('date', 'Not documented'),
            'unit': lab.get('unit', '')
        })
    
    return {
        'status': 'success',
        'message': f'Showing {len(formatted_labs)} most recent lab results',
        'lab_results': formatted_labs,
        'total_count': len(labs)
    }


def get_summary(data):
    """
    Get executive summary with risk assessment.
    Returns high-level overview for conversational context.
    """
    print("[HANDLER] Processing summary query")
    
    exec_summary = data['enhanced'].get('executive_summary', {})
    
    return {
        'status': 'success',
        'patient_name': exec_summary.get('patient_demographics', {}).get('name', 'Not documented'),
        'age': exec_summary.get('patient_demographics', {}).get('age', 'Not documented'),
        'risk_level': exec_summary.get('risk_level', 'Not assessed'),
        'narrative_summary': exec_summary.get('narrative_summary', 'No summary available')[:500],  # Limit length
        'key_summary_points': exec_summary.get('key_summary_points', [])[:5],  # Top 5 points
        'key_high_risk_conditions': exec_summary.get('key_high_risk_conditions', [])[:5],
        'overall_risk_assessment': exec_summary.get('overall_risk_assessment', 'Not assessed')[:400]
    }


def get_vitals_trend(data):
    """Get vital signs trends over time"""
    print("[HANDLER] Processing vitals_trend query")
    
    vitals_trend = None
    if data['medical']:
        vitals_trend = data['medical'].get('vitals_trend', {})
    
    if not vitals_trend or not vitals_trend.get('dates'):
        return {
            'status': 'no_data',
            'message': 'No vitals trend data available',
            'vitals_trend': {}
        }
    
    # Get vitals summary
    vitals_summary = data['medical'].get('vitals_summary', 'No vitals summary available')
    vitals_risk = data['medical'].get('vitals_risk_level', 'Not assessed')
    
    return {
        'status': 'success',
        'message': 'Vitals trend data available',
        'vitals_trend': {
            'dates': vitals_trend.get('dates', []),
            'bp': vitals_trend.get('bp', []),
            'heart_rate': vitals_trend.get('heart_rate', []),
            'weight': vitals_trend.get('weight', [])
        },
        'vitals_summary': vitals_summary[:400],  # Limit length
        'vitals_risk_level': vitals_risk
    }


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
        
        # Try to get from parameters array (Bedrock Agent format)
        if 'parameters' in event:
            for param in event['parameters']:
                if param.get('name') == 'sessionId':
                    session_id = param.get('value')
                elif param.get('name') == 'queryType':
                    query_type = param.get('value')
        
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
            raise ValueError("Missing required parameter: sessionId")
        
        print(f"[PARAMS] sessionId={session_id}, queryType={query_type}")
        
        # Load patient data from S3
        data = load_patient_data_from_s3(session_id)
        
        # Route to appropriate handler
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
            result = {
                'status': 'error',
                'message': f'Unknown query type: {query_type}',
                'available_types': ['family_history', 'patient_info', 'medications', 'conditions', 'lab_results', 'vitals_trend', 'summary']
            }
        
        # Return response in Bedrock Agent format
        response_body = {
            'application/json': {
                'body': json.dumps(result)
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
                    'error': str(e),
                    'message': f'Failed to query patient data: {str(e)}'
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
    # Test event
    test_event = {
        "actionGroup": "query-patient-data",
        "apiPath": "/query-patient-data",
        "httpMethod": "POST",
        "parameters": [
            {"name": "sessionId", "value": "65158ada-3c17-42dd-b836-8ef8622fea77"},
            {"name": "queryType", "value": "family_history"}
        ]
    }
    
    result = lambda_handler(test_event, None)
    print("\n" + "="*80)
    print("LAMBDA RESULT:")
    print(json.dumps(result, indent=2))

import streamlit as st
import pandas as pd
import boto3
import json
import time
import uuid
import os
import logging
import re
import random
import html
from datetime import datetime
from botocore.config import Config
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# Import PDF viewer utilities
from pdf_viewer_utils import (
    initialize_pdf_session_state,
    load_pdf_from_s3,
    render_pdf_page_with_highlights,
    navigate_to_page_with_condition,
    get_condition_evidence_bboxes,
    get_pdf_page_count,
    merge_bbox_from_both_sources,
    create_evidence_dict_from_bboxes,
    search_boxes_by_text,
    extract_bboxes_from_boxes_array,
    find_lab_result_bboxes
)

# Configure logging for developer debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =========================================================
# PAGE CONFIG
# =========================================================
st.set_page_config(
    page_title="APS Risk Assessment - Deloitte",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# =========================================================
# CUSTOM CSS - DELOITTE BRANDING
# =========================================================
st.markdown("""
<style>
    /* Deloitte brand colors: Black (#000000), White (#FFFFFF), Green (#86BC25) */
    
    /* Compact layout with proper header spacing */
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 1rem !important;
    }
    
    /* Reduce spacing between elements */
    .element-container {
        margin-bottom: 0.3rem !important;
    }
    
    /* Reduce vertical gaps between Streamlit elements */
    .stMarkdown {
        margin-bottom: 0 !important;
    }
    
    /* Compact file uploader */
    [data-testid="stFileUploader"] {
        padding: 0.5rem 0 !important;
    }
    
    /* Compact tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.5rem;
    }
    
    /* Primary buttons */
    .stButton > button[kind="primary"] {
        background-color: #86BC25 !important;
        color: #000000 !important;
        border: none !important;
        font-weight: 600 !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #75A821 !important;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2) !important;
    }
    
    /* Progress bar */
    .stProgress > div > div > div {
        background-color: #86BC25 !important;
    }
    
    /* Success messages */
    .stSuccess {
        background-color: rgba(134, 188, 37, 0.1) !important;
        border-left: 4px solid #86BC25 !important;
    }
    
    /* Links */
    a {
        color: #86BC25 !important;
    }
    a:hover {
        color: #75A821 !important;
    }
    
    /* Radio buttons selected state */
    .st-emotion-cache-1y4p8pa {
        color: #86BC25 !important;
    }
    
    /* Compact metrics */
    [data-testid="stMetricValue"] {
        font-size: 1.5rem;
    }
    
    /* Reduce heading margins */
    h1, h2, h3 {
        margin-top: 0.3rem !important;
        margin-bottom: 0.3rem !important;
    }
    
    /* Tighter column gaps */
    [data-testid="column"] {
        padding: 0 0.25rem !important;
    }
    
    /* Page navigation buttons styled as hyperlinks */
    .stButton > button[kind="secondary"] {
        background: none !important;
        border: none !important;
        padding: 0 !important;
        color: #86BC25 !important;
        text-decoration: none !important;
        font-weight: 500 !important;
        box-shadow: none !important;
        transition: all 0.2s ease !important;
    }
    
    .stButton > button[kind="secondary"]:hover {
        background: none !important;
        color: #75A821 !important;
        text-decoration: underline !important;
        box-shadow: none !important;
        transform: none !important;
    }
    
    .stButton > button[kind="secondary"]:focus {
        background: none !important;
        box-shadow: none !important;
        outline: none !important;
    }
    
    .stButton > button[kind="secondary"]:active {
        background: none !important;
        color: #5D8A19 !important;
    }
    
    /* Enhanced color-coded summary boxes */
    .stAlert > div {
        border-left-width: 4px !important;
    }
    
    /* Warning - Orange for moderate risk */
    .stWarning {
        background-color: rgba(237, 139, 0, 0.1) !important;
        border-left-color: #ED8B00 !important;
        color: #000000 !important;
    }
    
    /* Error - Red for high risk */
    .stError {
        background-color: rgba(220, 53, 69, 0.1) !important;
        border-left-color: #DC3545 !important;
        color: #000000 !important;
    }
    
    /* Prevent ghost/blur rendering during state transitions */
    .stChatMessage {
        transition: none !important;
        will-change: auto !important;
    }
    
    /* Ensure clean section rendering */
    .element-container {
        isolation: isolate;
    }
    
    /* Fix blurry text during spinner/loading states */
    .stSpinner {
        position: relative;
        z-index: 100;
        background: white;
    }
</style>
""", unsafe_allow_html=True)

# =========================================================
# AWS CONFIGURATION
# =========================================================
# Load configuration from config.local.json

def load_config():
    """Load configuration from config.local.json if it exists"""
    config_path = os.path.join(os.path.dirname(__file__), "config.local.json")
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        st.warning(f"⚠️ Error loading config.local.json: {e}. Using default AWS credentials.")
        return None

try:
    config = load_config()
    
    if config:
        # Use config file settings
        AWS_PROFILE = config.get("aws_profile", "default")
        AWS_REGION = config.get("aws_region", "us-east-1")
        BUCKET_NAME = config.get("s3_bucket", "aps-summarization-poc")
        AGENT_ID = config.get("bedrock_agent_id", "7TGNDBPOHR")  # Managing Agent
        AGENT_ALIAS_ID = config.get("bedrock_agent_alias_id", "PZS6QFL7BA")  # Updated alias
        
        # Create a session with the specified profile
        boto_session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        
        # Create clients using the session
        s3_client = boto_session.client("s3")
        bedrock_agent_client = boto_session.client(
            "bedrock-agent-runtime",
            config=Config(
                read_timeout=1200,  # 20 min timeout for long-running agent (6-step pipeline)
                connect_timeout=60
            )
        )
        bedrock_agent_mgmt_client = boto_session.client("bedrock-agent")

        AWS_CONFIGURED = True   
    else:
        # Use default AWS credentials (environment variables, IAM role, etc.)
        AWS_PROFILE = "default"
        AWS_REGION = "us-east-1"
        BUCKET_NAME = "aps-summarization-poc"
        AGENT_ID = "7TGNDBPOHR"  # Managing Agent
        AGENT_ALIAS_ID = "PZS6QFL7BA"  # Updated alias
        
        # Create clients with default credentials
        s3_client = boto3.client("s3", region_name=AWS_REGION)
        bedrock_agent_client = boto3.client(
            "bedrock-agent-runtime",
            region_name=AWS_REGION,
            config=Config(
                read_timeout=1200,  # 20 min timeout for long-running agent (6-step pipeline)
                connect_timeout=60
            )
        )
        bedrock_agent_mgmt_client = boto3.client("bedrock-agent", region_name=AWS_REGION)
    
        AWS_CONFIGURED = True   
        
except Exception as e:
    st.error(f"⚠️ AWS Configuration Error: {e}")
    AWS_CONFIGURED = False
    AWS_PROFILE = "N/A"
    AWS_REGION = "us-east-1"
    BUCKET_NAME = "aps-summarization-poc"
    AGENT_ID = "7TGNDBPOHR"  # Managing Agent (fallback)
    AGENT_ALIAS_ID = "PZS6QFL7BA"  # Updated alias (fallback)

# =========================================================
# PIPELINE STEPS (6 Steps - maps to 6 Lambda functions)
# =========================================================
PIPELINE_STEPS = [
    {"id": 1, "name": "OCR & Text Extraction", "icon": "📄", "description": "Extracting text and identifying medical images"},
    {"id": 2, "name": "Field Calculation", "icon": "🧮", "description": "Computing age, BMI, demographics, and extracting sections"},
    {"id": 3, "name": "Medical Coding", "icon": "🏥", "description": "Assigning ICD-10 and SNOMED-CT codes"},
    {"id": 4, "name": "Diagnostic Test Detection", "icon": "🔍", "description": "Identifying test pages/images (multimodal)"},
    {"id": 5, "name": "Diagnostic Analysis", "icon": "📊", "description": "Analyzing test results (multimodal)"},
    {"id": 6, "name": "Risk Assessment & Summary", "icon": "⚠️", "description": "Identifying high-risk elements and generating final summary"}
]

# =========================================================
# STEP MAPPING FOR TOOL TRACKING
# =========================================================
STEP_MAP = {
    "ocr": 1,
    "textract": 1,
    "extraction": 1,
    "document": 1,
    "calculate": 2,
    "field": 2,
    "bmi": 2,
    "section": 2,
    "icd": 3,
    "snomed": 3,
    "code": 3,
    "comprehend": 3,
    "detect": 4,
    "test": 4,
    "image": 4,
    "analyze": 5,
    "diagnostic": 5,
    "risk": 6,
    "highlight": 6,
    "assessment": 6,
    "summary": 6,
    "aggregat": 6,
    "generation": 6
}

# =========================================================
# SESSION STATE INITIALIZATION
# =========================================================
if "session_id" not in st.session_state:
    st.session_state.session_id = None

if "uploaded" not in st.session_state:
    st.session_state.uploaded = False

if "agent_running" not in st.session_state:
    st.session_state.agent_running = False

if "agent_completed" not in st.session_state:
    st.session_state.agent_completed = False

if "agent_error" not in st.session_state:
    st.session_state.agent_error = None

if "final_data" not in st.session_state:
    st.session_state.final_data = None

if "current_step" not in st.session_state:
    st.session_state.current_step = 0

if "uploaded_file_name" not in st.session_state:
    st.session_state.uploaded_file_name = None

if "step_status" not in st.session_state:
    # Track status of each pipeline step: "pending", "running", "success", "failed"
    st.session_state.step_status = ["pending"] * len(PIPELINE_STEPS)

if "current_tool" not in st.session_state:
    st.session_state.current_tool = None

if "show_dashboard" not in st.session_state:
    st.session_state.show_dashboard = False

if "dashboard_section" not in st.session_state:
    st.session_state.dashboard_section = "Summary"

if "pdf_viewer_visible" not in st.session_state:
    st.session_state.pdf_viewer_visible = True  # Default to open

if "user_prompt" not in st.session_state:
    st.session_state.user_prompt = None  # User's natural language request

if "agent_response_text" not in st.session_state:
    st.session_state.agent_response_text = None  # Store agent's text response

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []  # List of {role: "user"|"assistant"|"system", content: str, agent: str, timestamp: datetime}

if "conversation_active" not in st.session_state:
    st.session_state.conversation_active = False

if "selected_agent" not in st.session_state:
    st.session_state.selected_agent = None  # Track which agent is handling request

if "quick_action_selected" not in st.session_state:
    st.session_state.quick_action_selected = None

# Initialize PDF viewer session state
initialize_pdf_session_state()

# =========================================================
# FDA-APPROVED VITAL RANGES & CONSTANTS
# =========================================================
VITAL_RANGES = {
    "bmi": {
        "underweight": (0, 18.5),
        "normal": (18.5, 24.9),
        "overweight": (25, 29.9),
        "obese": (30, 100)
    },
    "bp_systolic": {
        "low": (0, 89),
        "normal": (90, 119),
        "elevated": (120, 129),
        "high_stage1": (130, 139),
        "high_stage2": (140, 250)
    },
    "bp_diastolic": {
        "low": (0, 59),
        "normal": (60, 79),
        "high_stage1": (80, 89),
        "high_stage2": (90, 150)
    },
    "heart_rate": {
        "bradycardia": (0, 59),
        "normal": (60, 100),
        "tachycardia": (101, 200)
    }
}

# =========================================================
# HELPER FUNCTIONS
# =========================================================

def get_vital_status(vital_type, value, age=None):
    """
    Determine status (color) of a vital sign based on age-specific ranges.
    
    Args:
        vital_type: Type of vital ('bmi', 'bp_systolic', 'bp_diastolic', 'heart_rate')
        value: Numeric value or string to evaluate
        age: Optional patient age for age-specific thresholds (defaults to adult if not provided)
    
    Returns:
        dict: {"status": "normal"|"borderline"|"critical", "color": "green"|"amber"|"red", "label": str}
    """
    if vital_type not in VITAL_RANGES:
        return {"status": "unknown", "color": "gray", "label": "Unknown"}
    
    # Handle N/A or non-numeric values
    if value == "N/A" or value is None:
        return {"status": "unknown", "color": "gray", "label": "N/A"}
    
    # Parse numeric value from string
    try:
        if isinstance(value, str):
            # Extract first number from string (e.g., "120/80 mmHg" -> 120)
            import re
            match = re.search(r'\d+\.?\d*', value)
            if match:
                numeric_value = float(match.group())
            else:
                return {"status": "unknown", "color": "gray", "label": str(value)}
        else:
            numeric_value = float(value)
    except (ValueError, TypeError):
        return {"status": "unknown", "color": "gray", "label": str(value)}
    
    # Determine age group for dynamic thresholds
    age_group = "adult"  # Default
    try:
        age_num = int(age) if age and str(age).strip() and age != "N/A" else 30
    except (ValueError, TypeError):
        age_num = 30
    
    if age_num < 1/12:
        age_group = "newborn"
    elif age_num < 1:
        age_group = "infant"
    elif age_num < 13:
        age_group = "child"
    elif age_num < 18:
        age_group = "adolescent"
    elif age_num <= 65:
        age_group = "adult"
    else:
        age_group = "elderly"
    
    # Use age-specific ranges for BMI (other vitals use fixed adult ranges from VITAL_RANGES)
    ranges = VITAL_RANGES[vital_type]
    
    # BMI classification - Age-specific ranges
    if vital_type == "bmi":
        # Define age-specific BMI thresholds matching Lambda logic
        if age_group == "newborn" or age_group == "infant":
            # BMI not applicable for newborns/infants (use weight-for-length)
            return {"status": "unknown", "color": "gray", "label": "N/A (age <1yr)"}
        elif age_group == "child":
            # Child: 15-20 normal
            if numeric_value < 14:
                return {"status": "critical", "color": "#DC3545", "label": "Severe Underweight"}
            elif numeric_value < 15:
                return {"status": "borderline", "color": "#ED8B00", "label": "Underweight"}
            elif numeric_value <= 20:
                return {"status": "normal", "color": "#86BC25", "label": "Normal"}
            elif numeric_value <= 25:
                return {"status": "borderline", "color": "#ED8B00", "label": "Overweight"}
            else:
                return {"status": "critical", "color": "#DC3545", "label": "Obese"}
        elif age_group == "adolescent":
            # Adolescent: 18-23 normal
            if numeric_value < 16:
                return {"status": "critical", "color": "#DC3545", "label": "Severe Underweight"}
            elif numeric_value < 18:
                return {"status": "borderline", "color": "#ED8B00", "label": "Underweight"}
            elif numeric_value <= 23:
                return {"status": "normal", "color": "#86BC25", "label": "Normal"}
            elif numeric_value <= 28:
                return {"status": "borderline", "color": "#ED8B00", "label": "Overweight"}
            else:
                return {"status": "critical", "color": "#DC3545", "label": "Obese"}
        elif age_group == "elderly":
            # Elderly (>65): 23-30 optimal (wider range to prevent frailty)
            if numeric_value < 20:
                return {"status": "critical", "color": "#DC3545", "label": "Severe Underweight"}
            elif numeric_value < 23:
                return {"status": "borderline", "color": "#ED8B00", "label": "Underweight"}
            elif numeric_value <= 30:
                return {"status": "normal", "color": "#86BC25", "label": "Normal"}
            elif numeric_value <= 35:
                return {"status": "borderline", "color": "#ED8B00", "label": "Overweight"}
            else:
                return {"status": "critical", "color": "#DC3545", "label": "Obese"}
        else:  # Adult (18-65)
            # Adult: 18.5-24.9 normal (WHO standard)
            if numeric_value < 16:
                return {"status": "critical", "color": "#DC3545", "label": "Severe Underweight"}
            elif numeric_value < 18.5:
                return {"status": "borderline", "color": "#ED8B00", "label": "Underweight"}
            elif numeric_value <= 24.9:
                return {"status": "normal", "color": "#86BC25", "label": "Normal"}
            elif numeric_value <= 29.9:
                return {"status": "borderline", "color": "#ED8B00", "label": "Overweight"}
            else:  # >= 30
                return {"status": "critical", "color": "#DC3545", "label": "Obese"}
    
    # Blood Pressure (Systolic) - Individual metric (simplified, use combined function for accurate classification)
    elif vital_type == "bp_systolic":
        if numeric_value < ranges["low"][1]:
            return {"status": "critical", "color": "#DC3545", "label": "Low"}
        elif ranges["normal"][0] <= numeric_value < ranges["elevated"][1]:  # Normal range extended to <130
            return {"status": "normal", "color": "#86BC25", "label": "Normal"}
        elif ranges["high_stage1"][0] <= numeric_value < ranges["high_stage1"][1]:
            return {"status": "borderline", "color": "#ED8B00", "label": "Elevated"}
        else:
            return {"status": "critical", "color": "#DC3545", "label": "High"}
    
    # Blood Pressure (Diastolic) - Individual metric (simplified, use combined function for accurate classification)
    elif vital_type == "bp_diastolic":
        if numeric_value < ranges["low"][1]:
            return {"status": "critical", "color": "#DC3545", "label": "Low"}
        elif ranges["normal"][0] <= numeric_value < ranges["high_stage1"][0]:  # Normal range <80
            return {"status": "normal", "color": "#86BC25", "label": "Normal"}
        elif ranges["high_stage1"][0] <= numeric_value < ranges["high_stage2"][0]:
            return {"status": "borderline", "color": "#ED8B00", "label": "Elevated"}
        else:
            return {"status": "critical", "color": "#DC3545", "label": "High"}
    
    # Heart Rate
    elif vital_type == "heart_rate":
        if numeric_value < ranges["bradycardia"][1]:
            return {"status": "borderline", "color": "#ED8B00", "label": "Bradycardia"}
        elif ranges["normal"][0] <= numeric_value <= ranges["normal"][1]:
            return {"status": "normal", "color": "#86BC25", "label": "Normal"}
        else:
            return {"status": "critical", "color": "#DC3545", "label": "Tachycardia"}
    
    return {"status": "unknown", "color": "gray", "label": "Unknown"}


def get_bp_combined_status(systolic_value, diastolic_value):
    """
    Determine combined blood pressure status using AHA 2017 guidelines.
    Requires BOTH systolic and diastolic values for accurate classification.
    
    Args:
        systolic_value: Systolic BP value (numeric or string)
        diastolic_value: Diastolic BP value (numeric or string)
    
    Returns:
        dict: {"status": str, "color": str, "label": str, "category": str}
    
    AHA 2017 Categories:
        - Normal: <120 AND <80
        - Elevated: 120-129 AND <80
        - Stage 1 HTN: 130-139 OR 80-89
        - Stage 2 HTN: ≥140 OR ≥90
        - Hypotension: <90 OR <60
    """
    # Parse numeric values
    try:
        if systolic_value is None or diastolic_value is None:
            return {"status": "unknown", "color": "gray", "label": "N/A", "category": "unknown"}
        
        # Handle string values
        if isinstance(systolic_value, str):
            systolic_value = systolic_value.replace('mmHg', '').replace('mm Hg', '').strip()
        if isinstance(diastolic_value, str):
            diastolic_value = diastolic_value.replace('mmHg', '').replace('mm Hg', '').strip()
        
        sys_num = float(systolic_value)
        dia_num = float(diastolic_value)
        
    except (ValueError, TypeError, AttributeError):
        return {"status": "unknown", "color": "gray", "label": "N/A", "category": "unknown"}
    
    # AHA 2017 Classification Logic
    # Hypotension (Critical Low)
    if sys_num < 90 or dia_num < 60:
        return {
            "status": "critical",
            "color": "#DC3545",
            "label": "Hypotension",
            "category": "hypotension"
        }
    
    # Stage 2 Hypertension (Critical High)
    if sys_num >= 140 or dia_num >= 90:
        return {
            "status": "critical",
            "color": "#DC3545",
            "label": "Stage 2 HTN",
            "category": "stage2_htn"
        }
    
    # Stage 1 Hypertension (Borderline)
    if (130 <= sys_num < 140) or (80 <= dia_num < 90):
        return {
            "status": "borderline",
            "color": "#ED8B00",
            "label": "Stage 1 HTN",
            "category": "stage1_htn"
        }
    
    # Elevated BP (Borderline) - MUST have systolic 120-129 AND diastolic <80
    if 120 <= sys_num < 130 and dia_num < 80:
        return {
            "status": "borderline",
            "color": "#ED8B00",
            "label": "Elevated BP",
            "category": "elevated"
        }
    
    # Normal BP
    if sys_num < 120 and dia_num < 80:
        return {
            "status": "normal",
            "color": "#86BC25",
            "label": "Normal BP",
            "category": "normal"
        }
    
    # Fallback (shouldn't reach here with valid values)
    return {"status": "unknown", "color": "gray", "label": "Unknown", "category": "unknown"}

HIGH_COLOR = "#DA291C"      # red
MODERATE_COLOR = "#ED8B00"  # orange
LOW_COLOR = "#86BC25"       # green


def highlight_risk_terms(text: str, high_terms=None, moderate_terms=None, low_terms=None) -> str:
    """
    Returns HTML with matched terms wrapped in colored/bold spans.
    Simplified flexible matching: highlights significant medical terms from keywords.
    Supports three risk levels: high (red), moderate (orange), low (green).
    Safe against HTML injection because we escape the input first.
    """
    if not text:
        return ""

    high_terms = [t.strip() for t in (high_terms or []) if t and t.strip()]
    moderate_terms = [t.strip() for t in (moderate_terms or []) if t and t.strip()]
    low_terms = [t.strip() for t in (low_terms or []) if t and t.strip()]

    if not high_terms and not moderate_terms and not low_terms:
        return html.escape(text)

    # Extract significant medical terms from keywords (simple word extraction)
    # Common stop words to ignore
    STOP_WORDS = {"of", "the", "a", "an", "for", "in", "on", "at", "to", "is", "and", "or", "with", "from"}
    
    # Medical abbreviation mapping
    MEDICAL_ABBREV = {
        "BP": "blood pressure",
        "HR": "heart rate",
        "BMI": "body mass index",
        "vitals": "vital signs"
    }
    
    def extract_terms(keyword_list):
        """
        Extract matchable terms from keywords.
        Now that LLM extracts VERBATIM phrases, we only need:
        1. Exact keyword phrases
        2. Abbreviation expansion (safety net for LLM inconsistencies)
        3. Synonym handling (vitals ↔ vital signs)
        """
        terms = []
        for keyword in keyword_list:
            # Add original keyword (exact phrase match)
            terms.append(keyword)
            
            # Handle abbreviations - add expanded versions (safety net)
            keyword_upper = keyword.upper()
            for abbr, expansion in MEDICAL_ABBREV.items():
                if abbr.upper() in keyword_upper:
                    terms.append(keyword.replace(abbr, expansion).replace(abbr.lower(), expansion))
            
            # Handle vitals ↔ vital signs synonym
            if "vital signs" in keyword.lower():
                terms.append(keyword.replace("vital signs", "vitals").replace("Vital signs", "Vitals"))
            elif "vitals" in keyword.lower() and "vital signs" not in keyword.lower():
                terms.append(keyword.replace("vitals", "vital signs").replace("Vitals", "Vital signs"))
        
        return terms
    
    high_terms_extracted = extract_terms(high_terms)
    moderate_terms_extracted = extract_terms(moderate_terms)
    low_terms_extracted = extract_terms(low_terms)
    
    # Build lookup: normalized term -> level (high > moderate > low priority)
    level_by_term = {}
    for t in low_terms_extracted:
        level_by_term[t.lower()] = "low"
    for t in moderate_terms_extracted:
        level_by_term[t.lower()] = "moderate"
    for t in high_terms_extracted:
        level_by_term[t.lower()] = "high"
    
    # Remove duplicates and sort by length (longest first to prevent partial matches)
    unique_terms = sorted(set(level_by_term.keys()), key=len, reverse=True)
    
    if not unique_terms:
        return html.escape(text)
    
    # Escape original text first
    escaped = html.escape(text)
    
    # Build regex pattern with word boundaries
    pattern = re.compile(
        r"(?<!\w)(" + "|".join(re.escape(t) for t in unique_terms) + r")(?!\w)",
        flags=re.IGNORECASE
    )
    
    def repl(m: re.Match) -> str:
        matched = m.group(1)
        level = level_by_term.get(matched.lower(), "moderate")
        if level == "high":
            color = HIGH_COLOR
        elif level == "low":
            color = LOW_COLOR
        else:
            color = MODERATE_COLOR
        return f'<span style="color:{color}; font-weight:700;">{matched}</span>'
    
    return pattern.sub(repl, escaped)

def render_colored_summary(summary_text, title="", show_bullets=False, key_points=None, high_risk_terms=None,moderate_risk_terms=None, low_risk_terms=None, risk_level=None, keyword_source="narrative_summary"):
    """
    Render summary with color coding based on LLM-generated risk level.
    
    Args:
        summary_text: Summary text to display
        title: Optional title prefix
        show_bullets: If True, show key points in expander
        key_points: Pre-generated key points from LLM (preferred over extraction)
        high_risk_terms: Terms to highlight in red (optional override)
        moderate_risk_terms: Terms to highlight in orange (optional override)
        low_risk_terms: Terms to highlight in green (optional override)
        risk_level: Risk level for border color (optional override, defaults to executive_summary.risk_level)
        keyword_source: Which keyword field to use - "narrative_summary" or "risk_assessment" (default: "narrative_summary")
    
    Returns:
        None (renders directly to Streamlit)
    """
    if not summary_text or summary_text == "N/A" or summary_text == "No summary available.":
        st.info("ℹ️ No summary available.")
        return
    
    # Get risk level from parameter or fall back to executive summary
    if risk_level is None:
        risk_level = "moderate"  # Default fallback
        if st.session_state.final_data:
            exec_summary = st.session_state.final_data.get("executive_summary", {})
            risk_level = exec_summary.get("risk_level", "moderate")

    # Get keywords from executive_summary if not provided
    if st.session_state.final_data and (high_risk_terms is None or moderate_risk_terms is None or low_risk_terms is None):
        exec_summary = st.session_state.final_data.get("executive_summary", {})
        
        # Select keyword source based on parameter
        if keyword_source == "risk_assessment":
            # Use risk_assessment_keywords for Risk Assessment section
            rl_keywords = exec_summary.get("risk_assessment_keywords", {}) or {}
        else:
            # Use narrative_summary_keywords for Summary section (default)
            rl_keywords = exec_summary.get("narrative_summary_keywords", {}) or {}
        
        high_risk_terms = high_risk_terms or (rl_keywords.get("high") or [])
        moderate_risk_terms = moderate_risk_terms or (rl_keywords.get("moderate") or [])
        low_risk_terms = low_risk_terms or (rl_keywords.get("low") or [])
    
    # CRITICAL: Only highlight keywords matching the overall risk level
    # This prevents confusion - if risk is HIGH, only show red highlights, not orange/green
    filtered_high_terms = []
    filtered_moderate_terms = []
    filtered_low_terms = []
    
    if risk_level == "high":
        # Only highlight high-risk keywords (red)
        filtered_high_terms = high_risk_terms
    elif risk_level == "moderate":
        # Only highlight moderate-risk keywords (orange)
        filtered_moderate_terms = moderate_risk_terms
    else:  # risk_level == "low"
        # Only highlight low-risk keywords (green)
        filtered_low_terms = low_risk_terms
    
    # Highlight keywords inline in summary text
    highlighted = highlight_risk_terms(
        summary_text,
        high_terms=filtered_high_terms,
        moderate_terms=filtered_moderate_terms,
        low_terms=filtered_low_terms,
    )
    
    # Add title as HTML bold if provided (after keyword highlighting to avoid escaping)
    if title:
        highlighted = f"<strong>{html.escape(title)}</strong> {highlighted}"

    # Single render (no st.error/warning/success) to avoid duplicates
    if risk_level == "high":
        border = HIGH_COLOR
        bg = "#FDECEA"
    elif risk_level == "moderate":
        border = MODERATE_COLOR
        bg = "#FFF4E5"
    else:
        border = LOW_COLOR
        bg = "#EAF7E2"

    st.markdown(
        f"""
        <div style="
            border-left: 6px solid {border};
            background: {bg};
            padding: 0.85rem 1rem;
            border-radius: 0.35rem;
            line-height: 1.45;
        ">{highlighted}</div>
        """,
        unsafe_allow_html=True,
    )

    
    # Show key points if available (prioritize LLM-generated points)
    if show_bullets and len(summary_text) > 200:
        # Use LLM-generated key points if provided
        if key_points and isinstance(key_points, list) and len(key_points) > 0:
            with st.expander("📋 Show Key Points"):
                for point in key_points:
                    # Ensure point ends with period
                    point_text = point.strip()
                    if point_text and not point_text.endswith('.'):
                        point_text += '.'
                    st.markdown(f"• {point_text}")
        else:
            # Fallback to improved regex extraction if no LLM points
            with st.expander("📋 Show Key Points"):
                import re
                # Split on sentence endings (period + space + capital letter or end of string)
                sentences = re.split(r'\.\s+(?=[A-Z])', summary_text)
                # Filter and clean sentences
                clean_sentences = []
                for s in sentences:
                    s = s.strip()
                    if len(s) > 40 and not s.startswith('(') and not s.endswith(':'):
                        if not s.endswith('.'):
                            s += '.'
                        clean_sentences.append(s)
                
                # Show top 5 key points
                for sentence in clean_sentences[:5]:
                    st.markdown(f"• {sentence}")


def render_vitals_summary(vitals_text):
    """
    Render vitals summary with color coding and keyword highlighting based on vitals_risk_level.
    This provides independent risk assessment for current vital signs only.
    
    Args:
        vitals_text: Vitals summary text to display
    
    Returns:
        None (renders directly to Streamlit)
    """
    if not vitals_text or vitals_text == "N/A" or vitals_text == "No vitals trend data available for analysis.":
        st.info("ℹ️ No vitals analysis available.")
        return
    
    # Get vitals-specific risk level and keywords from medical_summary (independent of overall medical risk)
    vitals_risk = "moderate"  # Default fallback
    high_terms = []
    moderate_terms = []
    low_terms = []
    
    if st.session_state.final_data:
        # Check health_overview for vitals_risk_level and vitals_keywords
        health_overview = st.session_state.final_data.get("health_overview", {})
        if isinstance(health_overview, dict):
            vitals_risk = health_overview.get("vitals_risk_level", "moderate")
            vitals_keywords = health_overview.get("vitals_keywords", {})
            vitals_thresholds = health_overview.get("vitals_thresholds_applied", {})
            if isinstance(vitals_keywords, dict):
                high_terms = vitals_keywords.get("high", [])
                moderate_terms = vitals_keywords.get("moderate", [])
                low_terms = vitals_keywords.get("low", [])
    
    # Display threshold metadata if available
    if vitals_thresholds:
        age_group = vitals_thresholds.get("age_group", "").title()
        age = vitals_thresholds.get("age", "N/A")
        gender = vitals_thresholds.get("gender", "Unknown")
        # More visible threshold info (regular text instead of gray caption)
        st.markdown(f"<p style='font-size: 0.9rem; color: #666; margin-bottom: 0.5rem;'>📊 <strong>Classification based on:</strong> {age_group} ranges (Age: {age}, Gender: {gender})</p>", unsafe_allow_html=True)
    
    # Highlight keywords inline
    highlighted = highlight_risk_terms(
        vitals_text,
        high_terms=high_terms,
        moderate_terms=moderate_terms,
        low_terms=low_terms,
    )
    
    # Render with color based on vitals risk level (not overall medical history)
    if vitals_risk == "high":
        border = HIGH_COLOR
        bg = "#FDECEA"
    elif vitals_risk == "moderate":
        border = MODERATE_COLOR
        bg = "#FFF4E5"
    else:  # low
        border = LOW_COLOR
        bg = "#EAF7E2"
    
    st.markdown(
        f"""
        <div style="
            border-left: 6px solid {border};
            background: {bg};
            padding: 0.85rem 1rem;
            border-radius: 0.35rem;
            line-height: 1.45;
        ">{highlighted}</div>
        """,
        unsafe_allow_html=True,
    )


def bedrock_invoke_with_retry(invoke_func, max_retries=5, initial_delay=1.0):
    """
    Wrapper for Bedrock API calls with exponential backoff retry logic.
    
    Args:
        invoke_func: Lambda function that performs the Bedrock API call
        max_retries: Maximum number of retry attempts (default: 5)
        initial_delay: Initial delay in seconds (default: 1.0)
    
    Returns:
        Response from the Bedrock API call
    
    Raises:
        Exception: If all retries are exhausted
    """
    retryable_exceptions = (
        'ThrottlingException',
        'ModelTimeoutException', 
        'ServiceUnavailableException',
        'InternalServerException',
        'TooManyRequestsException'
    )
    
    for attempt in range(max_retries):
        try:
            return invoke_func()
        except Exception as e:
            error_code = getattr(e, 'response', {}).get('Error', {}).get('Code', '')
            error_message = str(e)
            
            # Check if error is retryable
            is_retryable = any(exc in error_code or exc in error_message for exc in retryable_exceptions)
            
            if not is_retryable or attempt == max_retries - 1:
                # Non-retryable error or last attempt - raise immediately
                logger.error(f"[BEDROCK RETRY] Non-retryable error or max retries reached: {e}")
                raise
            
            # Calculate exponential backoff with jitter
            delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"[BEDROCK RETRY] Attempt {attempt + 1}/{max_retries} failed: {error_code or error_message}")
            logger.info(f"[BEDROCK RETRY] Retrying in {delay:.2f} seconds...")
            time.sleep(delay)
    
    # Should never reach here, but just in case
    raise Exception(f"Failed after {max_retries} retries")

def reset_session():
    """Reset session state for new upload"""
    st.session_state.session_id = None
    st.session_state.uploaded = False
    st.session_state.agent_running = False
    st.session_state.agent_completed = False
    st.session_state.agent_error = None
    st.session_state.final_data = None
    st.session_state.current_step = 0
    st.session_state.step_status = ["pending"] * len(PIPELINE_STEPS)
    st.session_state.current_tool = None
    st.session_state.show_dashboard = False
    st.session_state.user_prompt = None  # Reset user prompt
    st.session_state.agent_response_text = None  # Reset agent response
    st.session_state.chat_messages = []  # Reset chat history
    st.session_state.conversation_active = False
    st.session_state.selected_agent = None
    st.session_state.quick_action_selected = None
    # Remove dashboard_section from session state; it will be reinitialized on next run
    if "dashboard_section" in st.session_state:
        del st.session_state.dashboard_section


def wait_for_agent(client, agent_id, timeout=30):
    """Wait for agent to be in PREPARED status"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            status_response = client.get_agent(agentId=agent_id)
            status = status_response['agent']['agentStatus']
            logger.info(f"Current agent status: {status}")
            
            if status == 'PREPARED':
                logger.info("Agent is now prepared and ready!")
                return True
            elif status == 'FAILED':
                logger.error("Agent preparation failed.")
                raise Exception("Agent preparation failed.")
            
            time.sleep(5)
        except Exception as e:
            logger.warning(f"Error checking agent status: {e}")
            # Continue if it's just a permission issue, agent might still work
            return True
    
    # Timeout reached, but try to proceed anyway
    logger.warning(f"Timeout waiting for agent status, proceeding anyway...")
    return True


def process_response(response):
    """Process and combine response chunks"""
    completion = []
    for event in response["completion"]:
        if "chunk" in event:
            chunk = event["chunk"]["bytes"]
            completion.append(chunk)
    
    if completion:
        completion_full = b"".join(completion)
        completion_full = completion_full.decode("utf-8")
        return completion_full
    return ""


def invoke_bedrock_agent(s3_pdf_path, session_id, user_prompt):
    """
    Invoke Bedrock Managing Agent with the PDF path and user's natural language request.
    The managing agent will intelligently route to the appropriate collaborator based on the request.
    
    Args:
        s3_pdf_path: S3 URI of the PDF to process
        session_id: Unique session identifier
        user_prompt: User's natural language instruction/question
    """
    # Check if agent is ready before invoking
    logger.info("Checking agent status before invocation...")
    logger.info(f"Attempting to check agent: {AGENT_ID}")
    try:
        wait_for_agent(bedrock_agent_mgmt_client, AGENT_ID)
        logger.info("Agent is ready for invocation")
    except Exception as e:
        logger.warning(f"Could not verify agent status: {e}")
        logger.warning(f"Error type: {type(e).__name__}")
        logger.warning("Proceeding with invocation anyway...")
        # Don't fail here - some agents might not support status checks
        # The actual invocation will fail if there's a real problem
    
    # Build prompt with user request FIRST, then document context as "Available Resources"
    # This allows the agent to see the document info when it needs it, but makes routing decisions
    # based on the user's actual request, not the presence of document metadata
    agent_prompt = f"""User Request: {user_prompt}

--- Available Resources (for your reference if needed) ---
Document Location: {s3_pdf_path}
Session ID: {session_id}
Output Path: s3://{BUCKET_NAME}/{session_id}/outputs/"""
    
    logger.info(f"Invoking Managing Agent (ID: {AGENT_ID})")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"User prompt: {user_prompt[:200]}...")
    
    try:
        # Use retry wrapper for Bedrock invoke_agent call
        def invoke_bedrock():
            return bedrock_agent_client.invoke_agent(
                agentId=AGENT_ID,
                agentAliasId=AGENT_ALIAS_ID,
                sessionId=session_id,
                inputText=agent_prompt,
                enableTrace=True
            )
        
        response = bedrock_invoke_with_retry(invoke_bedrock)
        
        logger.info("Managing agent invoked successfully - will route based on user request")
        return response
    except Exception as e:
        logger.error(f"Error invoking Managing Agent: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Agent ID attempted: {AGENT_ID}")
        logger.error(f"Alias ID attempted: {AGENT_ALIAS_ID}")
        st.error(f"❌ Failed to start agent processing: {str(e)}")
        return None


def get_medical_images_from_s3(session_id):
    """Fetch all medical images from S3 for the given session"""
    try:
        prefix = f"{session_id}/outputs/medical_images/"
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=prefix
        )
        
        images = []
        if 'Contents' in response:
            for obj in response['Contents']:
                # Skip folders, only get image files
                if obj['Key'].endswith(('.png', '.jpg', '.jpeg', '.PNG', '.JPG', '.JPEG')):
                    # Extract page number from filename (e.g., unknown_page7_img0.png -> page 7)
                    filename = obj['Key'].split('/')[-1]
                    page_num = None
                    if 'page' in filename.lower():
                        try:
                            # Extract number after 'page'
                            page_part = filename.lower().split('page')[1]
                            page_num = int(''.join(filter(str.isdigit, page_part.split('_')[0])))
                        except:
                            pass
                    
                    images.append({
                        's3_key': obj['Key'],
                        'filename': filename,
                        'page': page_num,
                        's3_uri': f"s3://{BUCKET_NAME}/{obj['Key']}"
                    })
        
        logger.info(f"Found {len(images)} medical images in S3")
        return images
    except Exception as e:
        logger.error(f"Error fetching medical images from S3: {e}")
        return []
    
_word_re = re.compile(r"[A-Za-z0-9]+")

def to_camel_title(s: str) -> str:
    # "chronic_kidney-disease" -> "Chronic Kidney Disease"
    words = _word_re.findall(s or "")
    return " ".join(w[:1].upper() + w[1:].lower() for w in words) if words else ""


def parse_height_to_cm(height_str):
    """
    Parse height string in various formats and convert to centimeters.
    Supports: "5'10\"", "5'10", "5 ft 10 in", "178 cm", "178"
    Returns numeric cm value or None if parsing fails.
    """
    if not height_str:
        return None
    
    try:
        # If already numeric, assume it's cm
        if isinstance(height_str, (int, float)):
            return float(height_str)
        
        height_str = str(height_str).strip()
        
        # Check if already in cm format
        if 'cm' in height_str.lower():
            return float(re.search(r'[\d.]+', height_str).group())
        
        # Parse feet and inches format: 5'10", 5'10, 5 ft 10 in
        feet_inches_match = re.match(r"(\d+)['\s]*(?:ft|feet)?\s*(\d+)?[\"'\s]*(?:in|inches)?", height_str)
        if feet_inches_match:
            feet = int(feet_inches_match.group(1))
            inches = int(feet_inches_match.group(2)) if feet_inches_match.group(2) else 0
            total_inches = (feet * 12) + inches
            cm = total_inches * 2.54
            return round(cm, 1)
        
        # Try parsing as plain number (assume cm)
        numeric_match = re.search(r'[\d.]+', height_str)
        if numeric_match:
            return float(numeric_match.group())
            
    except (ValueError, AttributeError) as e:
        logger.warning(f"Could not parse height '{height_str}': {e}")
    
    return None


def transform_lambda_output_to_ui_format(raw_data, session_id=None, medical_images=None, medical_summary_data=None):
    """
    Dynamically transform Lambda output JSON to match UI's expected format.
    Intelligently extracts data from wherever it exists in the structure.
    Also processes medical_summary_data for encounters and family_history.
    """
    logger.info("Transforming Lambda output to UI format (dynamic mode)")
    
    def find_value(data, *keys):
        """Recursively search for a value in nested dict using multiple possible key names"""
        if not isinstance(data, dict):
            return None
        
        # Try direct keys first
        for key in keys:
            if key in data:
                return data[key]
        
        # Search nested structures
        for value in data.values():
            if isinstance(value, dict):
                result = find_value(value, *keys)
                if result is not None:
                    return result
        
        return None
    
    def find_list(data, *keys):
        """Recursively search for a list in nested dict"""
        if not isinstance(data, dict):
            return []
        
        for key in keys:
            if key in data and isinstance(data[key], list):
                return data[key]
        
        for value in data.values():
            if isinstance(value, dict):
                result = find_list(value, *keys)
                if result:
                    return result
        
        return []
    
    
    # Extract patient demographics - prioritize medical_summary data
    patient_name = None
    patient_age = None
    patient_sex = None
    patient_pcp = None
    patient_policy = None
    patient_dob = None
    patient_contact = None
    patient_address = None
    patient_occupation = None
    insurance_provider = None
    height = None
    weight = None
    bmi = None
    bp = None
    heart_rate = None
    summary_text = None
    substance_use_alcohol = None
    substance_use_tobacco = None
    substance_use_other = None
    
    # Check medical_summary first (has structured data)
    if medical_summary_data:
        med_summary = medical_summary_data.get("summary", {})
        overview = med_summary.get("overview", {})
        health_latest = med_summary.get("health_latest", {})
        insurance = med_summary.get("insurance", {})
        
        # Extract patient demographics from medical_summary
        patient_name = overview.get("name")
        patient_age = overview.get("age")
        patient_sex = overview.get("sex")
        patient_pcp = overview.get("pcp")
        patient_dob = overview.get("date_of_birth")
        patient_contact = overview.get("phone")
        patient_address = overview.get("address")
        patient_occupation = overview.get("occupation")
        patient_policy = insurance.get("ID_number") or insurance.get("policy_number")
        insurance_provider = insurance.get("provider")
        
        # Extract substance use data
        substance_use = med_summary.get("substance_use", {})
        substance_use_alcohol = substance_use.get("alcohol")
        substance_use_tobacco = substance_use.get("tobacco")
        substance_use_other = substance_use.get("other")
        
        # Extract health metrics from medical_summary
        height = health_latest.get("height")
        weight = health_latest.get("weight")
        bmi = health_latest.get("bmi")
        bp = health_latest.get("bp")
        heart_rate = health_latest.get("heart_rate")
        
        logger.info(f"Extracted patient data from medical_summary: {patient_name}, {patient_age}, {patient_sex}")
    
    # Fallback to enhanced summary if medical_summary not available
    if not patient_name:
        exec_summary = raw_data.get("executive_summary", {})
        patient_demographics = exec_summary.get("patient_demographics", {})
        
        patient_name = (patient_demographics.get("name") or 
                       find_value(raw_data, "name", "patient_name", "full_name", "Name"))
        patient_age = (patient_demographics.get("age") or 
                      find_value(raw_data, "age", "patient_age", "Age"))
        patient_sex = (patient_demographics.get("sex") or 
                      find_value(raw_data, "sex", "gender", "patient_sex", "Sex", "Gender"))
        patient_pcp = (patient_demographics.get("pcp") or 
                      find_value(raw_data, "pcp", "primary_care_physician", "physician", "doctor"))
        patient_policy = (patient_demographics.get("insurance_id") or 
                         patient_demographics.get("insurance_policy_number") or
                         find_value(raw_data, "policy_id", "policy_number", "insurance_id"))
        patient_dob = (patient_demographics.get("date_of_birth") or 
                      find_value(raw_data, "dob", "date_of_birth", "birth_date", "birthdate"))
        patient_contact = (patient_demographics.get("phone") or 
                          find_value(raw_data, "contact", "phone", "phone_number", "email"))
        patient_address = (patient_demographics.get("address") or 
                          find_value(raw_data, "address"))
        insurance_provider = (patient_demographics.get("insurance_provider") or 
                             find_value(raw_data, "insurance_provider", "insurance_company", "insurer"))
        
        health_latest_fallback = exec_summary.get("health_latest", {})
        height = height or health_latest_fallback.get("height") or find_value(raw_data, "height_cm", "height", "Height")
        weight = weight or health_latest_fallback.get("weight") or find_value(raw_data, "weight_kg", "weight", "Weight")
        bmi = bmi or health_latest_fallback.get("bmi") or find_value(raw_data, "bmi", "BMI", "body_mass_index")
        bp = bp or health_latest_fallback.get("bp") or find_value(raw_data, "bp", "BP", "blood_pressure")
        heart_rate = heart_rate or health_latest_fallback.get("heart_rate") or find_value(raw_data, "heart_rate", "hr", "pulse", "Heart Rate")
    
    # Extract summary text from executive_summary.narrative_summary (Pydantic model ensures consistency)
    exec_summary = raw_data.get("executive_summary", {})
    summary_text = exec_summary.get("narrative_summary")
    
    # Fallback to health_history if narrative_summary not available
    if not summary_text:
        summary_text = raw_data.get("health_history")
    if not summary_text and medical_summary_data:
        med_summary = medical_summary_data.get("summary", {})
        summary_text = med_summary.get("health_history")
    
    # Recommendations removed (not appropriate for UW use case)
    
    # Extract health concerns from categorized lists (new structure)
    # Try new categorized structure first
    high_risk_conditions = find_list(raw_data, "high_risk_conditions")
    chronic_conditions = find_list(raw_data, "chronic_conditions")
    other_conditions = find_list(raw_data, "other_conditions")
    
    # Extract medication buckets (new structure)
    high_risk_medications = find_list(raw_data, "high_risk_medications")
    other_medications = find_list(raw_data, "other_medications")
    
    # Fallback to legacy high_risk_findings if new structure not present
    if not high_risk_conditions and not chronic_conditions and not other_conditions:
        legacy_findings = find_list(raw_data, "high_risk_findings", "health_concerns", "risk_factors", "critical_findings")
        # Categorize legacy findings based on risk_level if present
        for finding in legacy_findings:
            risk_level = finding.get("risk_level", "").lower()
            if risk_level in ["critical", "high"]:
                high_risk_conditions.append(finding)
            elif risk_level == "medium":
                chronic_conditions.append(finding)
            else:
                other_conditions.append(finding)
    
    # Check for hierarchical_risk_analysis with separated SNOMED and RxNorm
    hierarchical_analysis = raw_data.get("hierarchical_risk_analysis", {})
    snomed_conditions = hierarchical_analysis.get("snomed_conditions", [])
    rxnorm_medications = hierarchical_analysis.get("rxnorm_medications", [])
    
    # Fallback to old 'conditions' field for backward compatibility
    if not snomed_conditions and not rxnorm_medications:
        hierarchical_conditions = hierarchical_analysis.get("conditions", [])
        # Separate manually if using old format
        for hc in hierarchical_conditions:
            if hc.get("code_type") == "RxNorm":
                rxnorm_medications.append(hc)
            else:
                snomed_conditions.append(hc)
    
    # Combine all sources with deduplication based on codes AND names
    health_concerns = []
    seen_codes = set()  # Track SNOMED/RxNorm codes to prevent duplicates
    seen_names = set()  # Track condition names (case-insensitive) to prevent duplicates
    
    # Helper function to get risk emoji based on condition_type
    def get_risk_emoji(finding):
        condition_type = finding.get("condition_type", "")
        if condition_type == "High Risk":
            return "🔴"
        elif condition_type == "Chronic":
            return "🟡"
        else:
            return "🟢"
    
    # Helper function to check if already seen
    def is_duplicate(condition_name, snomed_code, rxnorm_code):
        # Normalize name
        name_normalized = (condition_name or "").lower().strip()
        
        # Check codes
        if snomed_code and snomed_code in seen_codes:
            return True
        if rxnorm_code and rxnorm_code in seen_codes:
            return True
        
        # Check name
        if name_normalized and name_normalized in seen_names:
            return True
        
        return False
    
    # Helper function to track condition
    def track_condition(condition_name, snomed_code, rxnorm_code):
        if snomed_code:
            seen_codes.add(snomed_code)
        if rxnorm_code:
            seen_codes.add(rxnorm_code)
        name_normalized = (condition_name or "").lower().strip()
        if name_normalized:
            seen_names.add(name_normalized)
    
    # Process High Risk Conditions
    for finding in high_risk_conditions:
        condition_name_raw = finding.get("condition") or "Unknown Condition"
        condition_name = to_camel_title(condition_name_raw)
        snomed_code = finding.get("snomed_code")
        rxnorm_code = finding.get("rxnorm_code")
        
        if is_duplicate(condition_name, snomed_code, rxnorm_code):
            continue
        
        track_condition(condition_name, snomed_code, rxnorm_code)
        
        concern = {
            "risk_level": get_risk_emoji(finding),
            "condition_type": "High Risk",
            "title": condition_name,
            "description": finding.get("summary") or finding.get("description") or "",
            "page_reference": finding.get("page") or finding.get("page_number"),
            "snomed_code": snomed_code,
            "rxnorm_code": rxnorm_code,
            "icd10_code": finding.get("icd10_code"),
            "category": finding.get("category"),
            "original_condition": finding.get("original_condition"),
            "evidence": finding.get("evidence"),
            "evidence_boxes": finding.get("evidence_boxes", []),
            "code_type": "SNOMED",  # Always SNOMED for conditions (backend already separated meds)
            "date": finding.get("date"),
            "date_reported": finding.get("date_reported"),
            "source": "main_categorized",
            # Include hierarchical metadata for Summary tab grouping
            "hierarchy_depth": finding.get("hierarchy_depth", 0),
            "hierarchical_path": finding.get("hierarchical_path", []),
            "path_display": finding.get("path_display", "")
        }
        health_concerns.append(concern)
    
    # Process Chronic Conditions
    for finding in chronic_conditions:
        condition_name_raw = finding.get("condition") or "Unknown Condition"
        condition_name = to_camel_title(condition_name_raw)
        snomed_code = finding.get("snomed_code")
        rxnorm_code = finding.get("rxnorm_code")
        
        if is_duplicate(condition_name, snomed_code, rxnorm_code):
            continue
        
        track_condition(condition_name, snomed_code, rxnorm_code)
        
        concern = {
            "risk_level": get_risk_emoji(finding),
            "condition_type": "Chronic",
            "title": condition_name,
            "description": finding.get("summary") or finding.get("description") or "",
            "page_reference": finding.get("page") or finding.get("page_number"),
            "snomed_code": snomed_code,
            "rxnorm_code": rxnorm_code,
            "icd10_code": finding.get("icd10_code"),
            "category": finding.get("category"),
            "original_condition": finding.get("original_condition"),
            "evidence": finding.get("evidence"),
            "evidence_boxes": finding.get("evidence_boxes", []),
            "code_type": "SNOMED",  # Always SNOMED for conditions (backend already separated meds)
            "date": finding.get("date"),
            "date_reported": finding.get("date_reported"),
            "source": "main_categorized",
            # Include hierarchical metadata for potential grouping
            "hierarchy_depth": finding.get("hierarchy_depth", 0),
            "hierarchical_path": finding.get("hierarchical_path", []),
            "path_display": finding.get("path_display", "")
        }
        health_concerns.append(concern)
    
    # Process Other Conditions
    for finding in other_conditions:
        condition_name_raw = finding.get("condition") or "Unknown Condition"
        condition_name = to_camel_title(condition_name_raw)
        snomed_code = finding.get("snomed_code")
        rxnorm_code = finding.get("rxnorm_code")
        
        if is_duplicate(condition_name, snomed_code, rxnorm_code):
            continue
        
        track_condition(condition_name, snomed_code, rxnorm_code)
        
        concern = {
            "risk_level": get_risk_emoji(finding),
            "condition_type": "Other",
            "title": condition_name,
            "description": finding.get("summary") or finding.get("description") or "",
            "page_reference": finding.get("page") or finding.get("page_number"),
            "snomed_code": snomed_code,
            "rxnorm_code": rxnorm_code,
            "icd10_code": finding.get("icd10_code"),
            "category": finding.get("category"),
            "original_condition": finding.get("original_condition"),
            "evidence": finding.get("evidence"),
            "evidence_boxes": finding.get("evidence_boxes", []),
            "code_type": "SNOMED",  # Always SNOMED for conditions (backend already separated meds)
            "date": finding.get("date"),
            "source": "main_categorized"
        }
        health_concerns.append(concern)
    
    # Process High Risk Medications (from backend categorization)
    for finding in high_risk_medications:
        medication_name = finding.get("condition") or "Unknown Medication"
        snomed_code = finding.get("snomed_code")
        rxnorm_code = finding.get("rxnorm_code")
        
        if is_duplicate(medication_name, snomed_code, rxnorm_code):
            continue
        
        track_condition(medication_name, snomed_code, rxnorm_code)
        
        concern = {
            "risk_level": get_risk_emoji(finding),
            "condition_type": "High Risk",
            "title": medication_name,
            "description": finding.get("summary") or finding.get("description") or "",
            "page_reference": finding.get("page") or finding.get("page_number"),
            "snomed_code": snomed_code,
            "rxnorm_code": rxnorm_code,
            "icd10_code": finding.get("icd10_code"),
            "category": finding.get("category"),
            "original_condition": finding.get("original_condition"),
            "evidence": finding.get("evidence"),
            "evidence_boxes": finding.get("evidence_boxes", []),
            "code_type": "RxNorm",
            "date": finding.get("date"),
            "source": "main_categorized"
        }
        health_concerns.append(concern)
    
    # Process Other Medications (from backend categorization)
    for finding in other_medications:
        medication_name = finding.get("condition") or "Unknown Medication"
        snomed_code = finding.get("snomed_code")
        rxnorm_code = finding.get("rxnorm_code")
        
        if is_duplicate(medication_name, snomed_code, rxnorm_code):
            continue
        
        track_condition(medication_name, snomed_code, rxnorm_code)
        
        concern = {
            "risk_level": get_risk_emoji(finding),
            "condition_type": "Other",
            "title": medication_name,
            "description": finding.get("summary") or finding.get("description") or "",
            "page_reference": finding.get("page") or finding.get("page_number"),
            "snomed_code": snomed_code,
            "rxnorm_code": rxnorm_code,
            "icd10_code": finding.get("icd10_code"),
            "category": finding.get("category"),
            "original_condition": finding.get("original_condition"),
            "evidence": finding.get("evidence"),
            "evidence_boxes": finding.get("evidence_boxes", []),
            "code_type": "RxNorm",
            "date": finding.get("date"),
            "source": "main_categorized"
        }
        health_concerns.append(concern)
    
    # Process hierarchical SNOMED conditions
    for hc in snomed_conditions:
        condition_name_raw = hc.get("condition") or "Unknown Condition"
        condition_name = to_camel_title(condition_name_raw)
        snomed_code = hc.get("snomed_code")
        
        # Skip if already seen by code or name
        if is_duplicate(condition_name, snomed_code, None):
            continue
        
        track_condition(condition_name, snomed_code, None)
        
        # Use clinical_summary if available (generated by backend), otherwise build description
        description = hc.get("clinical_summary") or hc.get("description") or ""
        
        # If no clinical summary, build from available fields
        if not description:
            description_parts = []
            if hc.get("evidence"):
                description_parts.append(f"Found in document: {hc.get('evidence')}")
            if hc.get("path_display"):
                description_parts.append(f"Hierarchy: {hc.get('path_display')}")
            description = ". ".join(description_parts) if description_parts else "High-risk condition identified through hierarchical analysis"
        
        concern = {
            "risk_level": "🔴" if str(hc.get("risk_level", "")).lower() in ["high", "critical"] else "🟡",
            "title": condition_name,
            "description": description,
            "page_reference": hc.get("page"),
            "snomed_code": snomed_code,
            "icd10_code": hc.get("icd10_code"),
            "category": hc.get("category"),
            "original_condition": hc.get("original_condition") or hc.get("evidence"),
            "evidence": hc.get("evidence"),
            "evidence_boxes": hc.get("evidence_boxes", []),
            "date_reported": hc.get("date_reported"),
            "code_type": "SNOMED",
            "source": "hierarchical_snomed"
        }
        health_concerns.append(concern)
    
    # Process hierarchical RxNorm medications
    for hc in rxnorm_medications:
        condition_name_raw = hc.get("condition") or "Unknown Medication"
        condition_name = to_camel_title(condition_name_raw)
        rxnorm_code = hc.get("rxnorm_code") or hc.get("snomed_code")
        
        # Skip if already seen by code or name
        if is_duplicate(condition_name, None, rxnorm_code):
            continue
        
        track_condition(condition_name, None, rxnorm_code)
        
        # Use clinical_summary if available (generated by backend), otherwise build description  
        description = hc.get("clinical_summary") or hc.get("description") or ""
        
        # If no clinical summary, build from available fields
        if not description:
            description_parts = []
            if hc.get("evidence"):
                description_parts.append(f"Found in document: {hc.get('evidence')}")
            if hc.get("path_display"):
                description_parts.append(f"Hierarchy: {hc.get('path_display')}")
            description = ". ".join(description_parts) if description_parts else "High-risk medication identified through hierarchical analysis"
        
        concern = {
            "risk_level": "🔴" if str(hc.get("risk_level", "")).lower() in ["high", "critical"] else "🟡",
            "title": condition_name,
            "description": description,
            "page_reference": hc.get("page"),
            "rxnorm_code": rxnorm_code,
            "icd10_code": hc.get("icd10_code"),
            "category": hc.get("category"),
            "original_condition": hc.get("original_condition") or hc.get("evidence"),
            "evidence": hc.get("evidence"),
            "evidence_boxes": hc.get("evidence_boxes", []),
            "date_reported": hc.get("date_reported"),
            "code_type": "RxNorm",
            "source": "hierarchical_rxnorm"
        }
        health_concerns.append(concern)
    
    # Log deduplication statistics
    total_before = len(high_risk_conditions) + len(chronic_conditions) + len(other_conditions) + len(high_risk_medications) + len(other_medications) + len(snomed_conditions) + len(rxnorm_medications)
    total_after = len(health_concerns)
    duplicates_removed = total_before - total_after
    logger.info(f"Extracted {total_after} unique health concerns from {total_before} total findings ({duplicates_removed} duplicates removed)")
    logger.info(f"  - {len(high_risk_conditions)} from high_risk_conditions")
    logger.info(f"  - {len(chronic_conditions)} from chronic_conditions")
    logger.info(f"  - {len(other_conditions)} from other_conditions")
    logger.info(f"  - {len(high_risk_medications)} from high_risk_medications")
    logger.info(f"  - {len(other_medications)} from other_medications")
    logger.info(f"  - {len(snomed_conditions)} from hierarchical SNOMED conditions")
    logger.info(f"  - {len(rxnorm_medications)} from hierarchical RxNorm medications")
    logger.info(f"  - {len(seen_codes)} unique medical codes tracked")
    logger.info(f"  - {len(seen_names)} unique condition names tracked")
    
    # Extract coded conditions dynamically - handle both old and new grouped format
    coded_conditions_raw = raw_data.get("coded_conditions")
    
    # Check if it's the old format (dict with coded_entities) or new format (direct list)
    if isinstance(coded_conditions_raw, dict):
        coded_conditions = coded_conditions_raw.get("coded_entities") or []
    elif isinstance(coded_conditions_raw, list):
        coded_conditions = coded_conditions_raw
    else:
        coded_conditions = []
    
    # Collect all SNOMED codes from categorized conditions
    high_risk_snomed_codes = set()
    for f in high_risk_conditions:
        if f.get("snomed_code"):
            high_risk_snomed_codes.add(f.get("snomed_code"))
    for f in chronic_conditions:
        if f.get("snomed_code"):
            high_risk_snomed_codes.add(f.get("snomed_code"))
    for f in other_conditions:
        if f.get("snomed_code"):
            high_risk_snomed_codes.add(f.get("snomed_code"))

    other_conditions_list = []
    for cond in coded_conditions:
        # Handle grouped format (has primary_code, codes dict) vs legacy format
        if cond.get("primary_code"):
            # New grouped format
            codes = cond.get("codes", {})
            pages = cond.get("pages", [])
            dates = cond.get("dates", [])
            
            condition_data = {
                "condition": cond.get("condition") or "Unknown",
                "icd10": codes.get("icd10"),
                "snomed": codes.get("snomed"),
                "rxnorm": codes.get("rxnorm"),
                "description": cond.get("description"),
                "category": cond.get("category"),
                "primary_code": cond.get("primary_code"),
                "dates": dates,
                "most_recent_date": cond.get("most_recent_date"),
                "pages": pages,
                "evidence_count": cond.get("evidence_count", 0),
                "extracted_quote": cond.get("extracted_quote"),
                "risk_level": "High" if codes.get("snomed") in high_risk_snomed_codes else None,
                "page_reference": pages[0] if pages else None,
                "date_reported": cond.get("most_recent_date") or (dates[0] if dates else None),
            }
        else:
            # Legacy flat format
            snomed_val = cond.get("snomed_code") or cond.get("snomed") or cond.get("SNOMED")
            reported_date = cond.get("date_reported") or cond.get("diagnosis_date") or cond.get("reported_date")
            condition_data = {
                "condition": cond.get("condition") or cond.get("diagnosis") or cond.get("name") or "Unknown",
                "icd10": cond.get("icd10_code") or cond.get("icd10") or cond.get("ICD10"),
                "snomed": snomed_val,
                "rxnorm": cond.get("rxnorm_code") or cond.get("rxnorm") or cond.get("RxNorm"),
                "description": cond.get("icd10_description") or cond.get("rxnorm_description") or cond.get("snomed_description") or cond.get("description") or cond.get("details"),
                "category": cond.get("category"),
                "risk_level": "High" if snomed_val in high_risk_snomed_codes else None,
                "page_reference": cond.get("page") or cond.get("page_number") or cond.get("page_reference"),
                "date_reported": reported_date,
                "most_recent_date": reported_date,
            }
        other_conditions_list.append(condition_data)
    
    # Extract hospitalizations dynamically
    hospitalizations = find_list(raw_data, "hospitalizations", "hospital_visits", "admissions")
    hospitalization_list = []
    for hosp in hospitalizations:
        hosp_data = {
            "reason": hosp.get("reason") or hosp.get("diagnosis") or hosp.get("admission_reason"),
            "details": hosp.get("details") or hosp.get("description") or hosp.get("notes"),
            "date": hosp.get("date") or hosp.get("admission_date") or hosp.get("visit_date"),
            "page_reference": hosp.get("page") or hosp.get("page_number")
        }
        hospitalization_list.append(hosp_data)
    
    # Extract family history dynamically - check medical_summary first for array/string format
    family_history_list = []
    
    # Check if medical_summary has family_history (NEW: can be array or string)
    if medical_summary_data:
        med_summary = medical_summary_data.get("summary", {})
        family_history_data = med_summary.get("family_history")
        
        # Handle NEW array format
        if family_history_data and isinstance(family_history_data, list):
            for fh in family_history_data:
                if isinstance(fh, dict):
                    family_history_list.append({
                        "relation": fh.get("relation", "Unknown"),
                        "condition": fh.get("condition", "Unknown condition"),
                        "diagnosis_age": fh.get("diagnosis_age"),
                        "notes": fh.get("notes"),
                        "page_reference": None  # Will be enriched below
                    })
            logger.info(f"Extracted {len(family_history_list)} family history entries from array format")
        
        # Handle legacy string format (backward compatibility)
        elif family_history_data and isinstance(family_history_data, str) and family_history_data.lower() not in ["not specified", "not available", ""]:
            family_history_list.append({
                "relation": "Family",
                "condition": family_history_data,
                "diagnosis_age": None,
                "notes": None,
                "page_reference": None
            })
            logger.info(f"Extracted family_history from medical_summary (string): {family_history_data}")
    
    # If no array/string family history, try extracting structured list from enhanced summary
    if not family_history_list:
        family_history = find_list(raw_data, "family_history", "family_medical_history", "familial_conditions")
        for entry in family_history:
            family_entry = {
                "relation": entry.get("relation") or entry.get("relationship") or entry.get("family_member"),
                "condition": entry.get("condition") or entry.get("diagnosis") or entry.get("disease"),
                "diagnosis_age": entry.get("diagnosis_age") or entry.get("age_of_diagnosis"),
                "notes": entry.get("notes") or entry.get("details") or entry.get("description"),
                "page_reference": entry.get("page") or entry.get("page_number")
            }
            family_history_list.append(family_entry)
    
    # Enrich family history with page references from family_history_boxes if available
    if medical_summary_data and family_history_list:
        med_summary = medical_summary_data.get("summary", {})
        family_history_boxes = med_summary.get("family_history_boxes", [])
        
        if family_history_boxes:
            for entry in family_history_list:
                if not entry.get("page_reference"):
                    condition = entry.get("condition", "").lower()
                    # Search for matching box by condition text
                    for box in family_history_boxes:
                        box_text = box.get("line_text", "").lower()
                        # Look for family history keywords
                        if any(keyword in box_text for keyword in ["family", "history", "heart disease"]):
                            entry["page_reference"] = box.get("page")
                            break
            logger.info(f"Enriched family history with page references from family_history_boxes")
    
    # Extract diagnostic images/tests dynamically
    # Show ALL diagnostic tests (not just abnormal) for complete overview
    diagnostic_images = find_list(raw_data, "diagnostic_summaries", "diagnostic_summaries_likely_abnormal", "diagnostic_images", "test_results", "imaging_results")
    diagnostic_list = []
    
    # Create a map of page numbers to image paths from S3
    page_to_image = {}
    if medical_images:
        for img in medical_images:
            if img['page']:
                if img['page'] not in page_to_image:
                    page_to_image[img['page']] = []
                page_to_image[img['page']].append(img['s3_key'])
    
    for diag in diagnostic_images:
        # Extract page number from file_name if not directly provided
        page_ref = diag.get("page") or diag.get("page_number") or diag.get("page_reference")
        file_name = diag.get("file_name", "")
        
        if not page_ref and file_name and 'page' in file_name.lower():
            try:
                # Extract page number from filename like "unknown_page26_img0.png"
                page_part = file_name.lower().split('page')[1]
                page_ref = int(''.join(filter(str.isdigit, page_part.split('_')[0])))
            except:
                pass
        
        # Try to get image path from JSON first, then match by page number or filename
        image_path = diag.get("image_s3_uri") or diag.get("image_path") or diag.get("s3_path") or diag.get("image_uri")
        
        # If no image path in JSON, try to match by filename or page number
        if not image_path and medical_images:
            if file_name:
                # Try to find matching filename
                for img in medical_images:
                    if img['filename'] == file_name:
                        image_path = img['s3_key']
                        break
            
            # If still no match and we have page ref, try page number matching
            if not image_path and page_ref:
                try:
                    page_num = int(page_ref)
                    if page_num in page_to_image and page_to_image[page_num]:
                        image_path = page_to_image[page_num][0]
                except:
                    pass
        
        # Handle different field name variations for findings
        finding_text = None
        findings_array = diag.get("findings", [])
        impression = diag.get("impression", "")
        
        # Build finding text from available fields
        if impression:
            finding_text = impression
        elif findings_array:
            finding_text = "; ".join(findings_array) if isinstance(findings_array, list) else str(findings_array)
        else:
            finding_text = diag.get("finding") or diag.get("result") or diag.get("observation") or "No findings recorded"
        
        # Handle different field name variations for analysis
        analysis_text = (diag.get("abnormality_rationale") or 
                        diag.get("analysis") or 
                        diag.get("interpretation") or 
                        diag.get("notes") or 
                        diag.get("description") or 
                        "")
        
        diag_data = {
            "type": diag.get("test_type") or diag.get("type") or diag.get("imaging_type") or diag.get("modality") or "Diagnostic Test",
            "finding": finding_text,
            "analysis": analysis_text,
            "analysis_result": diag.get("abnormality_likelihood"),  # Add likelihood for filtering
            "test_date": diag.get("test_date"),  # Add test date
            "page_reference": page_ref,
            "image_path": image_path,
            "file_name": file_name
        }
        diagnostic_list.append(diag_data)
    
    # If we have images but no diagnostic summaries, create entries for all images
    if medical_images and not diagnostic_list:
        logger.info("No diagnostic summaries found, creating entries for all medical images")
        for img in medical_images:
            diagnostic_list.append({
                "type": "Medical Image",
                "finding": "Image from medical record",
                "analysis": "",
                "page_reference": img['page'],
                "image_path": img['s3_key']
            })
    
    logger.info(f"Created {len(diagnostic_list)} diagnostic image entries, {sum(1 for d in diagnostic_list if d.get('image_path')) } with image paths")
    
    # Sort diagnostic tests by date (most recent first)
    def parse_diagnostic_date(diag):
        date_str = diag.get("test_date")
        if not date_str or date_str == "N/A":
            return datetime(1900, 1, 1)
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except:
            return datetime(1900, 1, 1)
    
    diagnostic_list = sorted(diagnostic_list, key=parse_diagnostic_date, reverse=True)
    logger.info(f"Sorted {len(diagnostic_list)} diagnostic tests by date (most recent first)")
    
    # Extract lab results - prioritize medical_summary if available
    lab_results_raw = []
    if medical_summary_data:
        med_summary = medical_summary_data.get("summary", {})
        lab_results_raw = med_summary.get("lab_results", [])
        logger.info(f"Extracted {len(lab_results_raw)} lab results from medical_summary")
    
    # Fallback to enhanced summary
    if not lab_results_raw:
        lab_results_raw = raw_data.get("lab_results", [])
        if not lab_results_raw:
            lab_results_raw = find_list(raw_data, "lab_results", "laboratory_results", "labs", "test_results")
    
    lab_list = []
    for lab in lab_results_raw:
        # Case 1: structured dict (enhanced summary format)
        if isinstance(lab, dict):
            lab_data = {
                "test": lab.get("test_name") or lab.get("test") or lab.get("name"),
                "value": lab.get("value") or lab.get("result"),
                "normal_range": lab.get("normal_range") or lab.get("reference_range"),
                "risk": lab.get("status") or lab.get("risk") or lab.get("risk_level"),
                "date": lab.get("date"),
                "page_reference": lab.get("page") or lab.get("page_number"),
            }
            lab_list.append(lab_data)
        # Case 2: string rows (legacy format)
        elif isinstance(lab, str) and lab.strip():
            raw = lab.strip()

            # Light parsing: "left - right" => test/value; otherwise keep as test
            test_part = raw
            value_part = None
            if " - " in raw:
                left, right = raw.split(" - ", 1)
                test_part = left.strip() or raw
                value_part = right.strip() or None

            lab_list.append(
                {
                    "test": test_part,
                    "value": value_part,
                    "normal_range": None,
                    "risk": None,
                    "date": None,
                    "page_reference": None,
                }
            )
        else: 
            # Anything else: skip safely
            logger.warning(f"Skipping unsupported lab_results item type={type(lab).__name__}")
    
    # Enrich lab results with page references from lab_results_boxes if available
    if medical_summary_data:
        med_summary = medical_summary_data.get("summary", {})
        lab_results_boxes = med_summary.get("lab_results_boxes", [])
        
        if lab_results_boxes:
            # Create a mapping of test names to page numbers from boxes
            # Use enhanced matching that considers both test name and value
            for lab in lab_list:
                if not lab.get("page_reference"):
                    test_name = lab.get("test", "")
                    value = lab.get("value", "")
                    
                    # Search for matching box by test name and/or value
                    for box in lab_results_boxes:
                        box_text = box.get("line_text", "").lower()
                        test_lower = str(test_name).lower() if test_name else ""
                        value_lower = str(value).lower() if value else ""
                        
                        # Match if either test name or value is in the box text
                        # Prefer matches that have both
                        test_match = test_lower and test_lower in box_text
                        value_match = value_lower and value_lower in box_text
                        
                        if test_match and value_match:
                            # Strong match - both test and value found
                            lab["page_reference"] = box.get("page")
                            # Store bbox for later use
                            lab["_bbox"] = box.get("bbox")
                            break
                        elif test_match or value_match:
                            # Weak match - only one found, but keep looking for better match
                            if not lab.get("page_reference"):
                                lab["page_reference"] = box.get("page")
                                lab["_bbox"] = box.get("bbox")
            
            enriched_count = sum(1 for lab in lab_list if lab.get("page_reference"))
            logger.info(f"Enriched {enriched_count}/{len(lab_list)} lab results with page references from lab_results_boxes")
    
    # Sort lab results by date (most recent first)
    def parse_lab_date(lab):
        date_str = lab.get("date")
        if not date_str or date_str == "N/A":
            return datetime(1900, 1, 1)
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except:
            return datetime(1900, 1, 1)
    
    lab_list = sorted(lab_list, key=parse_lab_date, reverse=True)
    logger.info(f"Sorted {len(lab_list)} lab results by date (most recent first)")
    
    # Extract vitals trend dynamically - use vitals_trend from medical_summary (already formatted)
    vitals_trend = {}
    vitals_summary_text = None
    vitals_risk_level_text = "moderate"  # Default if not provided
    vitals_keywords_dict = {"high": [], "moderate": [], "low": []}  # Default keywords structure
    vitals_thresholds_applied = {}  # NEW: Age/gender threshold metadata
    
    if medical_summary_data:
        med_summary = medical_summary_data.get("summary", {})
        vitals_trend_raw = med_summary.get("vitals_trend", {})
        vitals_summary_text = med_summary.get("vitals_summary")
        vitals_risk_level_text = med_summary.get("vitals_risk_level", "moderate")  # NEW field
        vitals_keywords_dict = med_summary.get("vitals_keywords", {"high": [], "moderate": [], "low": []})  # Extract keywords
        vitals_thresholds_applied = med_summary.get("vitals_thresholds_applied", {})  # NEW: Extract threshold metadata
        
        if vitals_trend_raw and vitals_trend_raw.get("dates"):
            dates = vitals_trend_raw.get("dates", [])
            bp_raw = vitals_trend_raw.get("bp", [])
            heart_rate_raw = vitals_trend_raw.get("heart_rate", [])
            weight_raw = vitals_trend_raw.get("weight", [])
            
            # Filter and parse data, excluding "Not specified" or similar values
            filtered_dates = []
            bp_systolic = []
            bp_diastolic = []
            heart_rates = []
            weights = []
            
            for i, date in enumerate(dates):
                # Get values for this date index
                bp_str = bp_raw[i] if i < len(bp_raw) else None
                hr_str = heart_rate_raw[i] if i < len(heart_rate_raw) else None
                weight_str = weight_raw[i] if i < len(weight_raw) else None
                
                # Check if any value is valid (not "Not specified")
                has_valid_data = False
                bp_sys = None
                bp_dia = None
                hr_val = None
                wt_val = None
                
                # Parse BP (format: "148/92")
                if bp_str and isinstance(bp_str, str) and "/" in bp_str:
                    if "not" not in bp_str.lower() and "specified" not in bp_str.lower():
                        try:
                            sys, dia = bp_str.split("/")
                            bp_sys = int(sys.strip())
                            bp_dia = int(dia.strip())
                            has_valid_data = True
                        except:
                            pass
                
                # Parse heart rate
                if hr_str and isinstance(hr_str, str):
                    if "not" not in hr_str.lower() and "specified" not in hr_str.lower():
                        try:
                            hr_val = int(hr_str)
                            has_valid_data = True
                        except:
                            pass
                
                # Parse weight (remove "lb" suffix)
                if weight_str and isinstance(weight_str, str):
                    if "not" not in weight_str.lower() and "specified" not in weight_str.lower():
                        try:
                            wt_val = float(weight_str.replace("lb", "").strip())
                            has_valid_data = True
                        except:
                            pass
                
                # Only include this data point if at least one value is valid
                if has_valid_data:
                    filtered_dates.append(date)
                    bp_systolic.append(bp_sys)
                    bp_diastolic.append(bp_dia)
                    heart_rates.append(hr_val)
                    weights.append(wt_val)
            
            vitals_trend = {
                "dates": filtered_dates,
                "weights": weights,
                "bp_systolic": bp_systolic,
                "bp_diastolic": bp_diastolic,
                "heart_rate": heart_rates
            }
            logger.info(f"Extracted vitals_trend from medical_summary with {len(filtered_dates)} valid data points")
            logger.info(f"Date range: {filtered_dates[0] if filtered_dates else 'N/A'} to {filtered_dates[-1] if filtered_dates else 'N/A'}")
    
    # Fallback to existing vitals_trend from enhanced summary if not available
    if not vitals_trend or not vitals_trend.get("dates"):
        vitals_trend = find_value(raw_data, "vitals_trend", "vital_signs_trend", "trends") or {}
        logger.info(f"Using vitals_trend from enhanced summary: {vitals_trend}")
    
    # Extract enhanced medical summary fields - prioritize medical_summary structured data
    medical_history_raw = None
    surgical_history_raw = None
    surgeries_list = []
    hospitalizations_list = []
    medication_history_raw = None
    medical_appointments_raw = None
    lab_results_summary_text = None
    lab_results_keywords = {"high": [], "moderate": [], "low": []}
    lab_results_risk_level = "moderate"
    
    # Get from medical_summary if available (structured format)
    if medical_summary_data:
        med_summary = medical_summary_data.get("summary", {})
        medical_history_raw = med_summary.get("medical_history")  # String format
        surgical_history_raw = med_summary.get("surgical_history")  # String format (DEPRECATED)
        surgeries_list = med_summary.get("surgeries", [])  # NEW: Array of surgery dicts
        hospitalizations_list = med_summary.get("hospitalizations", [])  # NEW: Array of hospitalization dicts
        medication_history_raw = med_summary.get("medication_history")  # String format
        medical_appointments_raw = med_summary.get("medical_appointments", [])  # Array format
        lab_results_summary_text = med_summary.get("lab_results_summary")  # NEW: Lab summary text
        lab_results_keywords = med_summary.get("lab_results_keywords", {"high": [], "moderate": [], "low": []})  # NEW: Lab keywords
        lab_results_risk_level = med_summary.get("lab_results_risk_level", "moderate")  # NEW: Lab risk level
        logger.info(f"Extracted structured data from medical_summary: {len(medical_appointments_raw) if isinstance(medical_appointments_raw, list) else 0} appointments, {len(surgeries_list)} surgeries, {len(hospitalizations_list)} hospitalizations")
    
    # Fallback to enhanced summary if not available
    if medical_history_raw is None:
        medical_history_raw = raw_data.get("medical_history", [])
    if surgical_history_raw is None:
        surgical_history_raw = raw_data.get("surgical_history", [])
    if not surgeries_list:
        surgeries_list = raw_data.get("surgeries", [])
    if not hospitalizations_list:
        hospitalizations_list = raw_data.get("hospitalizations", [])
    if medication_history_raw is None:
        medication_history_raw = raw_data.get("medication_history", [])
    if not medical_appointments_raw:
        medical_appointments_raw = raw_data.get("medical_appointments", [])
    if not lab_results_summary_text:
        lab_results_summary_text = raw_data.get("lab_results_summary")
    if not lab_results_keywords or lab_results_keywords == {"high": [], "moderate": [], "low": []}:
        lab_results_keywords = raw_data.get("lab_results_keywords", {"high": [], "moderate": [], "low": []})
    if not lab_results_risk_level or lab_results_risk_level == "moderate":
        lab_results_risk_level = raw_data.get("lab_results_risk_level", "moderate")
    
    # Enrich medical appointments with page references from medical_appointments_boxes (exact text match)
    medical_appointments_list = []
    if medical_appointments_raw:
        if medical_summary_data:
            med_summary = medical_summary_data.get("summary", {})
            medical_appointments_boxes = med_summary.get("medical_appointments_boxes", [])
            
            logger.info(f"Enriching {len(medical_appointments_raw)} appointments using exact text matching with {len(medical_appointments_boxes)} bbox matches")
            
            for appt in medical_appointments_raw:
                appt_dict = {}
                
                # New format: [date, exact_quote, summary]
                if isinstance(appt, list) and len(appt) >= 3:
                    appt_date = appt[0].strip()
                    appt_exact_quote = appt[1].strip()  # Encounter header
                    appt_summary = appt[2].strip()  # Visit summary
                    
                    # Create appointment dict with all three fields
                    appt_dict = {
                        "date": appt_date,
                        "encounter": appt_exact_quote,  # Exact encounter header from PDF
                        "description": appt_summary,  # LLM-generated summary
                        "appointment": appt_exact_quote,  # Keep 'appointment' key for backward compatibility
                        "page_reference": None
                    }
                    
                    # Find matching bbox by searching for the exact_quote text (encounter header)
                    for bbox in medical_appointments_boxes:
                        bbox_value = bbox.get("value", "").strip()
                        line_text = bbox.get("line_text", "").strip()
                        
                        # Match if the exact_quote appears in the bbox value or line_text
                        if bbox_value and (appt_exact_quote in bbox_value or bbox_value in appt_exact_quote):
                            page = bbox.get("page")
                            appt_dict["page_reference"] = page
                            appt_dict["appointment_bbox"] = bbox
                            logger.info(f"Matched appointment '{appt_exact_quote[:50]}...' to page {page} via bbox value")
                            break
                        elif line_text and (appt_exact_quote in line_text or line_text in appt_exact_quote):
                            page = bbox.get("page")
                            appt_dict["page_reference"] = page
                            appt_dict["appointment_bbox"] = bbox
                            logger.info(f"Matched appointment '{appt_exact_quote[:50]}...' to page {page} via line_text")
                            break
                    
                    if not appt_dict.get("page_reference"):
                        logger.warning(f"No page match found for appointment: {appt_exact_quote[:100]}")
                
                # Fallback format: [date, exact_quote] (2-element, backward compatibility)
                elif isinstance(appt, list) and len(appt) == 2:
                    appt_date = appt[0].strip()
                    appt_description = appt[1].strip()
                    
                    # Create appointment dict with separate date and description
                    appt_dict = {
                        "date": appt_date,
                        "encounter": appt_description,
                        "description": "",  # No summary available
                        "appointment": appt_description,  # Keep 'appointment' key for compatibility
                        "page_reference": None
                    }
                    
                    # Find matching bbox by searching for the description text (NOT the date)
                    for bbox in medical_appointments_boxes:
                        bbox_value = bbox.get("value", "").strip()
                        line_text = bbox.get("line_text", "").strip()
                        
                        # Match if the description appears in the bbox value or line_text
                        if bbox_value and (appt_description in bbox_value or bbox_value in appt_description):
                            page = bbox.get("page")
                            appt_dict["page_reference"] = page
                            appt_dict["appointment_bbox"] = bbox
                            logger.info(f"Matched appointment '{appt_description[:50]}...' to page {page} via bbox value")
                            break
                        elif line_text and (appt_description in line_text or line_text in appt_description):
                            page = bbox.get("page")
                            appt_dict["page_reference"] = page
                            appt_dict["appointment_bbox"] = bbox
                            logger.info(f"Matched appointment '{appt_description[:50]}...' to page {page} via line_text")
                            break
                    
                    if not appt_dict.get("page_reference"):
                        logger.warning(f"No page match found for appointment: {appt_description[:100]}")
                
                # Legacy format support: "YYYY-MM-DD: Description (status)" string
                elif isinstance(appt, str):
                    appt_dict = {"appointment": appt, "page_reference": None}
                    
                    import re
                    # Strip status from appointment string for matching
                    appt_text_clean = re.sub(r'\s*\(\s*(completed|pending|missed|unknown|status unknown)\s*\)\s*$', '', appt, flags=re.IGNORECASE).strip()
                    
                    # Strip date prefix to match only the descriptive text
                    appt_text_clean = re.sub(r'^\d{4}-\d{2}-\d{2}:\s*', '', appt_text_clean).strip()
                    
                    # Find matching bbox
                    for bbox in medical_appointments_boxes:
                        bbox_value = bbox.get("value", "").strip()
                        line_text = bbox.get("line_text", "").strip()
                        
                        if bbox_value and (appt_text_clean in bbox_value or bbox_value in appt_text_clean):
                            page = bbox.get("page")
                            appt_dict["page_reference"] = page
                            appt_dict["appointment_bbox"] = bbox
                            logger.info(f"Matched legacy appointment '{appt_text_clean[:50]}...' to page {page} via bbox value")
                            break
                        elif line_text and (appt_text_clean in line_text or line_text in appt_text_clean):
                            page = bbox.get("page")
                            appt_dict["page_reference"] = page
                            appt_dict["appointment_bbox"] = bbox
                            logger.info(f"Matched legacy appointment '{appt_text_clean[:50]}...' to page {page} via line_text")
                            break
                    
                    if not appt_dict.get("page_reference"):
                        logger.warning(f"No page match found for legacy appointment: {appt_text_clean[:100]}")
                
                # Dict format (already enriched)
                elif isinstance(appt, dict):
                    appt_dict = appt
                    # If dict already has page_reference, keep it
                    if not appt_dict.get("page_reference") and medical_appointments_boxes:
                        appt_text = appt_dict.get("appointment", "")
                        if appt_text:
                            for bbox in medical_appointments_boxes:
                                bbox_value = bbox.get("value", "").strip()
                                line_text = bbox.get("line_text", "").strip()
                                
                                if bbox_value and (appt_text in bbox_value or bbox_value in appt_text):
                                    page = bbox.get("page")
                                    appt_dict["page_reference"] = page
                                    appt_dict["appointment_bbox"] = bbox
                                    break
                                elif line_text and (appt_text in line_text or line_text in appt_text):
                                    page = bbox.get("page")
                                    appt_dict["page_reference"] = page
                                    appt_dict["appointment_bbox"] = bbox
                                    break
                
                medical_appointments_list.append(appt_dict)
            
            matched_count = sum(1 for a in medical_appointments_list if a.get("page_reference"))
            logger.info(f"Enriched {matched_count}/{len(medical_appointments_list)} appointments with page references using EXACT TEXT matching")
        else:
            # No medical_summary, just use as-is
            for appt in medical_appointments_raw:
                if isinstance(appt, list) and len(appt) >= 2:
                    # New format: convert to dict
                    medical_appointments_list.append({
                        "date": appt[0].strip(),
                        "appointment": appt[1].strip(),
                        "page_reference": None
                    })
                elif isinstance(appt, str):
                    # Legacy format
                    medical_appointments_list.append({"appointment": appt, "page_reference": None})
                elif isinstance(appt, dict):
                    medical_appointments_list.append(appt)
    
    # Handle medical_history - can be array, object, or string
    if isinstance(medical_history_raw, list):
        # Enhanced summary format: medical_history is direct array of conditions
        medical_history_conditions = medical_history_raw
        medical_history_hospitalizations = hospitalizations  # Use the ones found earlier
    elif isinstance(medical_history_raw, dict):
        # Legacy format: medical_history has hospitalizations and other_conditions
        medical_history_conditions = medical_history_raw.get("other_conditions", [])
        medical_history_hospitalizations = medical_history_raw.get("hospitalizations", hospitalizations)
    elif isinstance(medical_history_raw, str):
        # String format from medical_summary - keep as is
        medical_history_conditions = medical_history_raw
        medical_history_hospitalizations = hospitalizations
    else:
        medical_history_conditions = []
        medical_history_hospitalizations = hospitalizations
    
    # Build transformed structure dynamically
    transformed = {
        "customer_details": {
            "name": patient_name or "N/A",
            "age": patient_age or "N/A",
            "sex": patient_sex or "N/A",
            "pcp": patient_pcp or "N/A",
            "occupation": patient_occupation or "N/A",
            "policy_id": patient_policy or "N/A",
            "dob": patient_dob or "N/A",
            "contact": patient_contact or "N/A",
            "address": patient_address or "N/A",
            "insurance_provider": insurance_provider or "N/A"
        },
        "substance_use": {
            "alcohol": substance_use_alcohol or "Not specified",
            "tobacco": substance_use_tobacco or "Not specified",
            "other": substance_use_other or "Not specified"
        },
        "summary": {
            "overall_summary_text": summary_text or "No summary available.",
            "overall_risk_assessment": exec_summary.get("overall_risk_assessment", "Risk assessment pending completion."),
            "health_concerns": health_concerns
        },
        "health_overview": {
            "height_cm": parse_height_to_cm(height) if height else None,  # Numeric cm for calculations
            "height_display": height or "N/A",  # Original string for display
            "weight_kg": weight or "N/A",
            "bmi": bmi or "N/A",
            "bp": bp or "N/A",
            "heart_rate": heart_rate or "N/A",
            "vitals_trend": vitals_trend,
            "vitals_summary": vitals_summary_text,
            "vitals_risk_level": vitals_risk_level_text,  # LLM-based risk for vitals only
            "vitals_keywords": vitals_keywords_dict,  # Keywords justifying vitals risk classification
            "vitals_thresholds_applied": vitals_thresholds_applied  # NEW: Age/gender threshold metadata
        },
        # "medical_history": {
        #     "hospitalizations": medical_history_hospitalizations,
        #     "other_conditions": medical_history_conditions if isinstance(medical_history_conditions, list) else []
        # },
        "medical_history": medical_history_raw,
        "hospitalizations": hospitalizations_list,  # NEW: Array of hospitalization dicts
        "surgeries": surgeries_list,  # NEW: Array of surgery dicts
        "surgical_history": surgical_history_raw,  # DEPRECATED: Keep for backward compatibility
        "medication_history": medication_history_raw,
        "medical_appointments": medical_appointments_list,
        "family_history": family_history_list,
        "diagnostic_images": diagnostic_list,
        "lab_results_summary": lab_results_summary_text,  # NEW: Lab summary text
        "lab_results_keywords": lab_results_keywords,  # NEW: Lab keywords for highlighting
        "lab_results_risk_level": lab_results_risk_level,  # NEW: Lab risk level
        "lab_results": lab_list,
        "coded_conditions": coded_conditions,
        # Categorized conditions and medications for Medical History tab (from risk analyzer)
        "high_risk_conditions": high_risk_conditions,
        "chronic_conditions": chronic_conditions,
        "other_conditions": other_conditions,
        "high_risk_medications": high_risk_medications,
        "other_medications": other_medications,
        # Grouped structures for umbrella categorization (NEW)
        "chronic_conditions_grouped": raw_data.get("chronic_conditions_grouped", {}),
        "other_conditions_grouped": raw_data.get("other_conditions_grouped", {}),
        "other_medications_grouped": raw_data.get("other_medications_grouped", {}),
        # Preserve executive_summary from Lambda output for UI Summary section
        "executive_summary": raw_data.get("executive_summary", {})
    }
    
    # Add bbox lookup arrays from medical_summary for highlighting
    if medical_summary_data:
        summary_section = medical_summary_data.get("summary", {})
        transformed["bbox_lookups"] = {
            "medical_history_boxes": summary_section.get("medical_history_boxes", []),
            "medication_history_boxes": summary_section.get("medication_history_boxes", []),
            "surgeries_boxes": summary_section.get("surgeries_boxes", []),  # NEW
            "surgical_history_boxes": summary_section.get("surgical_history_boxes", []),  # DEPRECATED: Keep for backward compat
            "hospitalizations_boxes": summary_section.get("hospitalizations_boxes", []),  # NEW
            "family_history_boxes": summary_section.get("family_history_boxes", []),
            "lab_results_boxes": summary_section.get("lab_results_boxes", []),
            "health_history_boxes": summary_section.get("health_history_boxes", []),
            "medical_appointments_boxes": summary_section.get("medical_appointments_boxes", []),
            "overview_boxes": summary_section.get("overview_boxes", {}),
            "insurance_boxes": summary_section.get("insurance_boxes", {}),
            "substance_use_boxes": summary_section.get("substance_use_boxes", {}),
            "health_latest_boxes": summary_section.get("health_latest_boxes", {})
        }
        logger.info(f"Loaded bbox lookups: {len(transformed['bbox_lookups']['medication_history_boxes'])} medication boxes, {len(transformed['bbox_lookups']['lab_results_boxes'])} lab boxes, {len(transformed['bbox_lookups'].get('health_history_boxes', []))} health history boxes, {len(transformed['bbox_lookups']['surgeries_boxes'])} surgery boxes, {len(transformed['bbox_lookups']['hospitalizations_boxes'])} hospitalization boxes")
    else:
        transformed["bbox_lookups"] = {}
        logger.warning("No medical_summary data available for bbox lookups")
    
    logger.info(f"Enhanced medical summary transformation complete: {len(other_conditions_list)} conditions, {len(health_concerns)} health concerns, {len(diagnostic_list)} diagnostic images, {len(lab_list)} lab results")
    logger.info(f"Medical history: {len(medical_history_conditions)} conditions, {len(medical_history_hospitalizations)} hospitalizations")
    logger.info(f"Surgical history: {len(surgical_history_raw)} surgeries, Medication history: {len(medication_history_raw)} medications")
    logger.info(f"Medical appointments: {len(medical_appointments_raw)} appointments")
    return transformed


def load_final_summary(session_id, max_retries=3, retry_delay=5):
    """
    Try to load final summary with limited retries (3 × 5s = 15s max)
    Also loads medical_summary.json for encounters and family_history data
    """
    final_key = f"{session_id}/outputs/enhanced_medical_summary_with_risks.json"
    medical_summary_key = f"{session_id}/outputs/medical_summary.json"
    
    for attempt in range(max_retries):
        try:
            # Load enhanced summary
            response = s3_client.get_object(Bucket=BUCKET_NAME, Key=final_key)
            raw_data = json.loads(response['Body'].read().decode('utf-8'))
            
            # Load medical_summary.json for encounters and family_history
            medical_summary_data = None
            try:
                med_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=medical_summary_key)
                medical_summary_data = json.loads(med_response['Body'].read().decode('utf-8'))
                logger.info("Successfully loaded medical_summary.json for encounters data")
            except s3_client.exceptions.NoSuchKey:
                logger.warning("medical_summary.json not found - vitals trend may be unavailable")
            except Exception as e:
                logger.warning(f"Error loading medical_summary.json: {e}")
            
            # Fetch medical images from S3
            medical_images = get_medical_images_from_s3(session_id)
            
            # Transform Lambda output to UI format (pass both JSONs)
            transformed_data = transform_lambda_output_to_ui_format(raw_data, session_id, medical_images, medical_summary_data)
            return transformed_data
        except s3_client.exceptions.NoSuchKey:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            continue
        except Exception as e:
            st.error(f"❌ Error loading final summary: {e}")
            return None
    
    return None


def get_s3_diagnostics(session_id):
    """Get detailed S3 diagnostics showing what files exist and their sizes"""
    try:
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=f"{session_id}/outputs/"
        )
        
        files_found = []
        if 'Contents' in response:
            for obj in response['Contents']:
                file_key = obj['Key'].split('/')[-1]
                file_size_kb = obj['Size'] / 1024
                files_found.append({
                    "File": file_key,
                    "Size (KB)": f"{file_size_kb:.1f}",
                    "Path": obj['Key']
                })
        
        return files_found
    except Exception as e:
        st.error(f"Error listing S3 objects: {e}")
        return []


def check_pipeline_outputs(session_id):
    """Check which pipeline outputs exist in S3 (matches actual lambda outputs)"""
    outputs_to_check = {
        "Step 1: Extracted Text": f"{session_id}/outputs/extracted_text.json",
        "Step 2: Medical Summary": f"{session_id}/outputs/medical_summary.json",
        "Step 3: ICD/SNOMED Codes": f"{session_id}/outputs/coded_conditions.json",
        "Step 4: Diagnostic Tests": f"{session_id}/outputs/diagnostic_tests.json",
        "Step 5: Diagnostic Analysis": f"{session_id}/outputs/diagnostic_summaries.json",
        "Step 6: Final Summary": f"{session_id}/outputs/enhanced_medical_summary_with_risks.json"
    }
    
    results = {}
    for name, key in outputs_to_check.items():
        try:
            s3_client.head_object(Bucket=BUCKET_NAME, Key=key)
            results[name] = "✅ Exists"
        except:
            results[name] = "❌ Missing"
    
    return results


def validate_single_step_from_s3(session_id, step_index, max_retries=2, retry_delay=1.5):
    """
    Validate a single pipeline step's output in S3 and update status.
    Retries with short delays to avoid blocking UI updates.
    Uses max 2 retries × 1.5s = 3s total wait time.
    """
    step_files = [
        f"{session_id}/outputs/extracted_text.json",  # Step 1
        f"{session_id}/outputs/medical_summary.json",  # Step 2
        f"{session_id}/outputs/coded_conditions.json",  # Step 3
        f"{session_id}/outputs/diagnostic_tests.json",  # Step 4
        f"{session_id}/outputs/diagnostic_summaries.json",  # Step 5
        f"{session_id}/outputs/enhanced_medical_summary_with_risks.json"  # Step 6
    ]
    
    if step_index >= len(step_files):
        return False
    
    file_key = step_files[step_index]
    
    # Quick retries to catch S3 eventual consistency
    for attempt in range(max_retries):
        try:
            s3_client.head_object(Bucket=BUCKET_NAME, Key=file_key)
            st.session_state.step_status[step_index] = "success"
            logger.info(f"✅ Step {step_index + 1} validated: {file_key.split('/')[-1]} exists in S3 (attempt {attempt + 1})")
            return True
        except:
            if attempt < max_retries - 1:
                logger.info(f"⏳ Step {step_index + 1}: Waiting for {file_key.split('/')[-1]} (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                logger.warning(f"⏳ Step {step_index + 1}: {file_key.split('/')[-1]} not found after {max_retries} attempts")
    
    return False


def validate_pipeline_status_from_s3(session_id):
    """Validate and update step_status based on actual S3 outputs"""
    logger.info(f"Validating pipeline from S3...")
    
    # Map of S3 files to pipeline steps (0-indexed)
    step_files = [
        f"{session_id}/outputs/extracted_text.json",  # Step 1
        f"{session_id}/outputs/medical_summary.json",  # Step 2
        f"{session_id}/outputs/coded_conditions.json",  # Step 3
        f"{session_id}/outputs/diagnostic_tests.json",  # Step 4
        f"{session_id}/outputs/diagnostic_summaries.json",  # Step 5
        f"{session_id}/outputs/enhanced_medical_summary_with_risks.json"  # Step 6
    ]
    
    # Track if we hit a failure to cascade to dependent steps
    has_failure = False
    success_count = 0
    
    # Check if medical images exist
    has_images = False
    try:
        images_prefix = f"{session_id}/outputs/medical_images/"
        images_response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=images_prefix,
            MaxKeys=1
        )
        has_images = 'Contents' in images_response and len(images_response['Contents']) > 0
    except:
        pass
    
    for idx, file_key in enumerate(step_files):
        # Special handling for Steps 4 & 5 when no images exist
        if (idx == 3 or idx == 4) and not has_images:
            # Mark as skipped if no images
            st.session_state.step_status[idx] = "skipped"
            success_count += 1  # Count skipped as progress
            logger.info(f"Step {idx + 1} skipped: No images in PDF")
            continue
        
        try:
            s3_client.head_object(Bucket=BUCKET_NAME, Key=file_key)
            # File exists - mark as success only if no previous failure
            if not has_failure:
                st.session_state.step_status[idx] = "success"
                success_count += 1
            else:
                # Previous step failed, so this shouldn't have succeeded (orphaned file)
                st.session_state.step_status[idx] = "success"
        except:
            # File doesn't exist
            has_failure = True
            st.session_state.step_status[idx] = "failed"
            logger.warning(f"Step {idx + 1} failed: {file_key.split('/')[-1]} not found")
            
            # Cascade failure to all remaining steps (except already skipped)
            for remaining_idx in range(idx + 1, len(step_files)):
                if st.session_state.step_status[remaining_idx] not in ["success", "skipped"]:
                    st.session_state.step_status[remaining_idx] = "failed"
            break  # No need to check remaining files
    
    # Log summary
    logger.info(f"Validation complete: {success_count}/{len(step_files)} steps succeeded")


def display_pipeline_progress(current_step):
    """Display pipeline progress with visual indicators based on actual step status"""
    st.markdown("### 🔄 Pipeline Progress")
    
    cols = st.columns(6)
    
    for idx, step in enumerate(PIPELINE_STEPS):
        with cols[idx]:
            status = st.session_state.step_status[idx]
            
            if status == "success":
                # Completed successfully
                st.markdown(f"<div style='text-align: center;'><span style='font-size: 30px;'>✅</span><br/><small><b>{step['name']}</b></small></div>", unsafe_allow_html=True)
            elif status == "running":
                # Currently running
                st.markdown(f"<div style='text-align: center;'><span style='font-size: 30px;'>⏳</span><br/><small><b style='color: #1f77b4;'>{step['name']}</b></small></div>", unsafe_allow_html=True)
            elif status == "skipped":
                # Skipped (e.g., no images for steps 4-5)
                st.markdown(f"<div style='text-align: center;'><span style='font-size: 30px;'>⏭️</span><br/><small><b style='color: #888;'>{step['name']}</b></small><br/><small style='color: #888;'>Skipped</small></div>", unsafe_allow_html=True)
            elif status == "failed":
                # Failed
                st.markdown(f"<div style='text-align: center;'><span style='font-size: 30px;'>❌</span><br/><small><b style='color: #d62728;'>{step['name']}</b></small></div>", unsafe_allow_html=True)
            else:
                # Pending
                st.markdown(f"<div style='text-align: center;'><span style='font-size: 30px; opacity: 0.3;'>{step['icon']}</span><br/><small>{step['name']}</small></div>", unsafe_allow_html=True)
    
    # Progress bar - calculate based on completed steps (count success and skipped as progress)
    completed_steps = sum(1 for status in st.session_state.step_status if status in ["success", "skipped"])
    progress_percentage = min(completed_steps / len(PIPELINE_STEPS), 1.0)
    st.progress(progress_percentage)


# =========================================================
# MAIN APP HEADER - DELOITTE BRANDING
# =========================================================

# Load and encode SVG for header
import base64
try:
    with open("deloitte_BIG.svg", "rb") as f:
        svg_data = base64.b64encode(f.read()).decode()
    header_logo_src = f'data:image/svg+xml;base64,{svg_data}'
except:
    header_logo_src = 'deloitte_BIG.png'

st.markdown(f"""
<style>
    .header-container {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1.5rem;
        padding: 1rem 1rem 0.75rem 1rem;
        margin-bottom: 0.75rem;
    }}
    .header-logo {{
        flex-shrink: 0;
        display: flex;
        align-items: center;
        order: 2;
    }}
    .header-logo img {{
        height: 30px;
        width: auto;
    }}
    .header-text {{
        flex-grow: 1;
        display: flex;
        flex-direction: column;
        justify-content: center;
        order: 1;
    }}
    .app-title {{
        color: #000000;
        font-size: 1.5rem;
        font-weight: 600;
        margin: 0;
        padding: 0;
        line-height: 1.2;
        font-family: 'Helvetica Neue', 'Arial', sans-serif;
    }}
    .app-subtitle {{
        color: #5F5F5F;
        font-size: 0.95rem;
        margin: 0.2rem 0 0 0;
        padding: 0;
        line-height: 1.2;
        font-family: 'Helvetica Neue', 'Arial', sans-serif;
    }}
</style>
<div class="header-container">
    <div class="header-text">
        <div class="app-title">Attending Physician Statement Risk Assessment</div>
        <div class="app-subtitle">Intelligent Medical Document Analysis for Underwriting</div>
    </div>
    <div class="header-logo">
        <img src="{header_logo_src}" alt="Deloitte" />
    </div>
</div>
<hr style="margin: 0.5rem 0 1rem 0; border: none; border-top: 3px solid #86BC25;"/>
""", unsafe_allow_html=True)

# =========================================================
# SIDEBAR - NAVIGATION ONLY (Configuration removed)
# =========================================================
with st.sidebar:
    # Sidebar header with application title
    st.markdown("""
    <div style='text-align: center; padding: 0.5rem 0; border-bottom: 2px solid #86BC25; margin-bottom: 0.75rem;'>
        <h3 style='color: #000000; margin: 0; padding: 0; font-size: 1.1rem; font-weight: 600; line-height: 1.2;'>
            Attending Physician Statement<br/>Summarization and Analysis
        </h3>
    </div>
    """, unsafe_allow_html=True)
    
    # if st.session_state.session_id:
    #     st.success(f"**Session:** `{st.session_state.session_id[:8]}...`")
    
    # Back to Chat Button (only shown when dashboard is displayed)
    if st.session_state.final_data and st.session_state.show_dashboard:
        if st.button("⬅️ Back to Chat", key="back_to_chat_sidebar", use_container_width=True):
            st.session_state.show_dashboard = False
            st.session_state.agent_completed = False
            st.session_state.agent_running = False
            # Preserve conversation state when returning to chat
            if not st.session_state.conversation_active:
                st.session_state.conversation_active = True
            st.rerun()
        
        st.markdown("---")
    
    # Navigation (only shown when dashboard is displayed)
    if st.session_state.final_data and st.session_state.show_dashboard:
        st.title("Navigation")
        st.radio(
            "Select Section",
            [
                "Summary",
                "Customer Details",
                "Health Overview",
                "Medical History",
                "Family History",
                "Diagnostic Images",
                "Lab Results"
            ],
            key="dashboard_section"
        )
    
    # New Assessment Button (only shown when a session is active)
    # Hidden during upload and initial state to prevent accidental resets
    if st.session_state.uploaded:
        st.markdown("---")
        if st.button("🔄 New Assessment", use_container_width=True):
            reset_session()
            st.rerun()

# =========================================================
# UPLOAD SECTION
# =========================================================
if not st.session_state.uploaded:
    
    st.header("Upload Attending Physician Statement (PDF)")
    
    if not AWS_CONFIGURED:
        st.error("⚠️ AWS is not configured properly. Please ensure config.local.json exists with valid aws_profile, aws_region, and s3_bucket settings.")
        st.stop()
    
    uploaded_file = st.file_uploader(
        "Choose an APS PDF file to summarize and analyze",
        type=["pdf"],
        help="Upload a PDF containing Attending Physician Statement"
    )
    
    if uploaded_file is not None:
        
        # Generate new session ID
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.uploaded_file_name = uploaded_file.name
        
        st.info(f"📋 **Session ID:** `{st.session_state.session_id}`")
        
        # Upload to S3
        with st.spinner("⬆️ Uploading PDF to S3..."):
            try:
                # S3 input key with original filename to prevent overwrites
                s3_input_key = f"{st.session_state.session_id}/input/{uploaded_file.name}"
                s3_client.upload_fileobj(uploaded_file, BUCKET_NAME, s3_input_key)
                st.success(f"✅ PDF uploaded successfully")
                
                logger.info(f"PDF uploaded to S3: s3://{BUCKET_NAME}/{s3_input_key}")
                logger.info(f"Session folder: s3://{BUCKET_NAME}/{st.session_state.session_id}/")
                
                st.session_state.uploaded = True
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"❌ Upload failed: {e}")

# =========================================================
# AGENT INVOCATION SECTION - CHATBOT INTERFACE
# =========================================================
elif st.session_state.uploaded and not st.session_state.agent_running and not st.session_state.show_dashboard:
    
    st.header("💬 Step 2: Chat with APS Analysis Agent")
    
    # Document info in collapsible section
    with st.expander("📄 Document Information", expanded=False):
        st.info(f"**PDF Ready:** {st.session_state.uploaded_file_name}")
        st.info(f"**Session ID:** `{st.session_state.session_id}`")
    
    st.markdown("---")
    
    # Display chat history
    if st.session_state.chat_messages:
        st.markdown("### 💬 Conversation")
        
        for message in st.session_state.chat_messages:
            with st.chat_message(message["role"]):
                if message["role"] == "system":
                    st.info(message["content"])
                elif message["role"] == "assistant":
                    # Show agent badge
                    if message.get("agent"):
                        # Determine agent type and color based on agent name
                        agent_name = message["agent"]
                        if "Summarization" in agent_name:
                            agent_type = "summarization"
                            badge_color = "#0066CC"  # Blue for Summarization Agent
                        elif "Processing" in agent_name:
                            agent_type = "processing"
                            badge_color = "#9B59B6"  # Purple for Processing Agent
                        else:
                            agent_type = "managing"
                            badge_color = "#ED8B00"  # Orange for Managing Agent
                        
                        st.markdown(
                            f'<div style="background: {badge_color}; color: white; padding: 0.3rem 0.6rem; '
                            f'border-radius: 0.8rem; font-size: 0.7rem; font-weight: 600; display: inline-block; '
                            f'margin-bottom: 0.5rem;">🤖 {agent_name}</div>',
                            unsafe_allow_html=True
                        )
                    st.markdown(message["content"])
                else:
                    st.markdown(message["content"])
        
        st.markdown("---")
    
    # Handle quick action selection
    if st.session_state.quick_action_selected:
        user_prompt = st.session_state.quick_action_selected
        st.session_state.quick_action_selected = None
        
        # Clean up old S3 outputs if retrying after error (prevents stale crosses in progress bar)
        if st.session_state.agent_error is not None:
            logger.info("Cleaning up old S3 outputs from previous failed attempt")
            try:
                # Delete old output files to prevent validation from showing stale "failed" status
                output_prefix = f"{st.session_state.session_id}/outputs/"
                delete_response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=output_prefix)
                if 'Contents' in delete_response:
                    for obj in delete_response['Contents']:
                        s3_client.delete_object(Bucket=BUCKET_NAME, Key=obj['Key'])
                        logger.info(f"Deleted old output: {obj['Key']}")
            except Exception as e:
                logger.warning(f"Could not clean old S3 outputs: {e}")
        
        # Reset agent state for new request (clear old errors/progress)
        st.session_state.agent_error = None
        st.session_state.agent_completed = False
        st.session_state.step_status = ["pending"] * len(PIPELINE_STEPS)
        st.session_state.current_step = 0
        
        # Add to chat history
        st.session_state.chat_messages.append({
            "role": "user",
            "content": user_prompt,
            "timestamp": datetime.now()
        })
        st.session_state.user_prompt = user_prompt
        st.session_state.agent_running = True
        st.rerun()
    
    # Chat input (disabled during processing)
    if st.session_state.agent_running:
        st.info("⏳ **Processing your request...** The agent is currently working. Please wait for the response.")
    
    user_input = st.chat_input(
        "Ask me anything about this APS document..." if not st.session_state.chat_messages else "Continue the conversation...",
        key="chat_input",
        disabled=st.session_state.agent_running  # Disable input during processing
    )
    
    if user_input and not st.session_state.agent_running:
        # Clean up old S3 outputs if retrying after error (prevents stale crosses in progress bar)
        if st.session_state.agent_error is not None:
            logger.info("Cleaning up old S3 outputs from previous failed attempt")
            try:
                # Delete old output files to prevent validation from showing stale "failed" status
                output_prefix = f"{st.session_state.session_id}/outputs/"
                delete_response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=output_prefix)
                if 'Contents' in delete_response:
                    for obj in delete_response['Contents']:
                        s3_client.delete_object(Bucket=BUCKET_NAME, Key=obj['Key'])
                        logger.info(f"Deleted old output: {obj['Key']}")
            except Exception as e:
                logger.warning(f"Could not clean old S3 outputs: {e}")
        
        # Reset agent state for new request (clear old errors/progress)
        st.session_state.agent_error = None
        st.session_state.agent_completed = False
        st.session_state.step_status = ["pending"] * len(PIPELINE_STEPS)
        st.session_state.current_step = 0
        
        # Add user message to chat history
        st.session_state.chat_messages.append({
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now()
        })
        st.session_state.user_prompt = user_input
        st.session_state.agent_running = True
        st.session_state.conversation_active = True
        st.rerun()
    
    # Quick Action Suggestion Chips (shown BELOW input when no conversation started)
    if not st.session_state.chat_messages and not st.session_state.agent_running:
        st.markdown("")
        st.markdown("##### 💡 Suggested prompts:")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("📋 Comprehensive Summary", use_container_width=True, help="Generate complete medical analysis with risk assessment", key="quick_1"):
                st.session_state.quick_action_selected = "Analyze this APS document and provide a comprehensive medical summary"
                st.rerun()
        
        with col2:
            if st.button("⚠️ Identify Risk Factors", use_container_width=True, help="Extract high-risk conditions and medications", key="quick_2"):
                st.session_state.quick_action_selected = "Analyze this document and identify all risk factors"
                st.rerun()
        
        with col3:
            if st.button("🧬 Extract Medical Codes", use_container_width=True, help="Get ICD-10 and SNOMED-CT codes", key="quick_3"):
                st.session_state.quick_action_selected = "Extract medical codes from this APS document"
                st.rerun()
    
    # Show dashboard button if results are available (after summarization completes)
    if st.session_state.final_data and not st.session_state.agent_running:
        st.markdown("---")
        st.markdown("### 🎯 Next Steps")
        st.caption("View the complete analysis dashboard or continue the conversation")
        
        if st.button("📊 View Full Dashboard", type="primary", use_container_width=True, key="dash_from_chat"):
            st.session_state.show_dashboard = True
            
            # Load PDF if not already loaded
            if not st.session_state.get("pdf_bytes"):
                with st.spinner("📄 Loading PDF..."):
                    try:
                        pdf_bytes = load_pdf_from_s3(
                            st.session_state.session_id,
                            BUCKET_NAME,
                            s3_client
                        )
                        if pdf_bytes:
                            st.session_state.pdf_bytes = pdf_bytes
                            st.session_state.pdf_page = 1
                            logger.info("PDF loaded successfully for viewer")
                        else:
                            logger.warning("PDF could not be loaded from S3")
                            st.session_state.pdf_bytes = None
                    except Exception as e:
                        logger.error(f"Error loading PDF from chat view: {e}")
                        st.session_state.pdf_bytes = None
            
            st.rerun()
    
    # Stop here to prevent further rendering while waiting for submission
    st.stop()

# =========================================================
# AGENT RUNNING SECTION (WITH PROGRESS)
# =========================================================
elif st.session_state.agent_running == True and not st.session_state.agent_completed and not st.session_state.show_dashboard:
    
    # Display header
    st.header("💬 APS Analysis Chat")
    
    # Show FULL conversation history (static, not blurred)
    # Display all previous messages normally (not in processing state)
    for message in st.session_state.chat_messages[:-1]:  # All except last (being processed)
        with st.chat_message(message["role"]):
            if message["role"] == "system":
                st.info(message["content"])
            elif message["role"] == "assistant":
                # Show agent badge
                if message.get("agent"):
                    # Determine agent type and color based on agent name
                    agent_name = message["agent"]
                    if "Summarization" in agent_name:
                        badge_color = "#0066CC"  # Blue for Summarization Agent
                    elif "Processing" in agent_name:
                        badge_color = "#9B59B6"  # Purple for Processing Agent
                    else:
                        badge_color = "#ED8B00"  # Orange for Managing Agent
                    
                    st.markdown(
                        f'<div style="background: {badge_color}; color: white; padding: 0.3rem 0.6rem; '
                        f'border-radius: 0.8rem; font-size: 0.7rem; font-weight: 600; display: inline-block; '
                        f'margin-bottom: 0.5rem;">🤖 {agent_name}</div>',
                        unsafe_allow_html=True
                    )
                st.markdown(message["content"])
            else:  # user message
                st.markdown(message["content"])
    
    # Show current message being processed
    if st.session_state.chat_messages:
        current_message = st.session_state.chat_messages[-1]
        if current_message["role"] == "user":
            with st.chat_message("user"):
                st.markdown(current_message["content"])
    
    st.markdown("---")
    
    # Use container for processing status
    with st.container():
        # Show processing in assistant message bubble
        with st.chat_message("assistant"):
            # Agent routing display
            agent_status_display = st.empty()
            agent_status_display.info("🔄 **Routing:** Managing Agent analyzing request...")
        
            # Live tool display banner (only for summarize mode)
            tool_display = st.empty()
            
            # Progress display container (updates in real-time) - use empty() for live updates
            progress_container = st.empty()
            
            # Track if agent encountered an error
            has_error = False
            
            # Track if we're in summarization mode (will be detected from trace events)
            is_summarization = False
            # Store in session state so it's available in Loading Results section
            st.session_state.tools_invoked = False
            
            # Invoke agent with user's natural language prompt
            s3_pdf_path = f"s3://{BUCKET_NAME}/{st.session_state.session_id}/input/{st.session_state.uploaded_file_name}"
            
            # Processing status section
            st.markdown("---")
            st.markdown("### 🔄 Processing Status")
            
            progress_messages = []
            
            # Create message placeholder
            message_placeholder = st.empty()
            
            def add_message(msg, level="info"):
                """Add message to UI if important; log only errors/warnings to reduce terminal spam"""
                # Only log errors and warnings to terminal (not info-level progress updates)
                if level == "error":
                    logger.error(msg)
                elif level == "warning":
                    logger.warning(msg)
                
                # Only show user-friendly messages in UI
                if any(emoji in msg for emoji in ["✅", "❌", "⚠️", "🔧", "✨", "🤔", "📋", "📊"]):
                    progress_messages.append(msg)
                    with message_placeholder.container():
                        for m in progress_messages[-8:]:  # Show last 8 important messages
                            st.markdown(f"- {m}")
            
            logger.info("Initiating Bedrock Agent...")
            add_message("🤔 Analyzing your request...")
            
            # Invoke agent with user's natural language prompt
            logger.info(f"User prompt: {st.session_state.user_prompt[:100]}...")
            logger.info("Invoking Managing Agent - will route to Summarization or Processing collaborator based on request")
            
            # Build conversation context if available
            conversation_context = ""
            if len(st.session_state.chat_messages) > 2:  # Has previous conversation
                recent_messages = st.session_state.chat_messages[-7:-1]  # Last 3 Q&A pairs (exclude current message)
                conversation_context = "\n\nPREVIOUS CONVERSATION CONTEXT:\n"
                for msg in recent_messages:
                    if msg["role"] == "user":
                        conversation_context += f"User: {msg['content']}\n"
                    elif msg["role"] == "assistant" and len(msg.get("content", "")) < 300:
                        conversation_context += f"Assistant: {msg['content']}\n"
            
            # CRITICAL FIX: Include actual patient data JSON in prompt for follow-up questions
            # This prevents hallucination by giving the agent access to the actual data
            json_data_context = ""
            if st.session_state.final_data:
                logger.info("Including patient data JSON in agent context to prevent hallucination")
                # Include complete JSON to ensure agent has all data for answering
                json_data_context = f"\n\n--- PATIENT DATA (USE THIS TO ANSWER ALL QUESTIONS) ---\n"
                json_data_context += f"CRITICAL INSTRUCTION: You MUST use ONLY the data below to answer questions. "
                json_data_context += f"NEVER fabricate or invent information not present in this JSON. "
                json_data_context += f"If a field shows 'Not specified', 'Unknown', or is empty, you MUST state that explicitly.\n\n"
                json_data_context += json.dumps(st.session_state.final_data, indent=2)
                json_data_context += f"\n\n--- END PATIENT DATA ---\n"
            
            # Append all context to user prompt
            enhanced_prompt = st.session_state.user_prompt
            if json_data_context:
                enhanced_prompt = f"{st.session_state.user_prompt}{json_data_context}"
            if conversation_context:
                enhanced_prompt += f"\n{conversation_context}"
            
            response = invoke_bedrock_agent(s3_pdf_path, st.session_state.session_id, enhanced_prompt)
            
            if not response:
                st.error("❌ Failed to start processing. Please check your AWS configuration.")
                logger.error("Agent invocation failed - check credentials, agent ID, and permissions")
                
                # Add error to chat history
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": "❌ I encountered an error while trying to process your request. Please check the AWS configuration and try again.",
                    "agent": "System",
                    "timestamp": datetime.now()
                })
                st.session_state.agent_running = False
                st.rerun()
                st.stop()
            
            # Debug: Log response structure (reduced verbosity)
            logger.debug(f"Response received - Type: {type(response)}")
            logger.debug(f"Response keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
            if 'completion' not in response:
                logger.warning("⚠️ 'completion' key not found in response!")
            
            # Lock to prevent double invocation on rerun
            st.session_state.agent_running = "started"
            logger.info("Agent invoked successfully, streaming events...")
            add_message("📋 Request received - routing to appropriate agent...")
            
            try:
                event_stream = response.get('completion', [])
                logger.debug(f"Event stream object type: {type(event_stream)}")
                
                agent_response_text = ""
                event_count = 0  # Track events for periodic S3 validation
                has_received_response = False  # Track if we've received any agent response yet
                
                for event in event_stream:
                    event_count += 1
                    
                    # Log events at debug level (reduced verbosity)
                    logger.debug(f"EVENT #{event_count} received - Type: {list(event.keys()) if isinstance(event, dict) else type(event)}")
                    
                    # CRITICAL FIX: If we've received many trace events but no chunks yet,
                    # proactively check S3 to detect if summarization pipeline is running
                    # Use file age check (< 2 minutes) to avoid false positives from old S3 files
                    if (event_count >= 5 and event_count % 3 == 0 and 
                        not is_summarization and not has_received_response):
                        logger.info(f"Proactive S3 check after {event_count} trace events (no chunks yet)...")
                        # Check if Step 1 output exists in S3 (extracted_text.json)
                        # AND check if it was created recently (within last 2 minutes) to avoid old files
                        try:
                            step1_key = f"{st.session_state.session_id}/outputs/extracted_text.json"
                            response = s3_client.head_object(Bucket=BUCKET_NAME, Key=step1_key)
                            file_age_seconds = (datetime.now(response['LastModified'].tzinfo) - response['LastModified']).total_seconds()
                            
                            # Only treat as current summarization if file was created within last 2 minutes
                            if file_age_seconds < 120:
                                # File exists and is fresh! This is summarization mode
                                is_summarization = True
                                st.session_state.tools_invoked = True
                                st.session_state.selected_agent = "APS Summarization Agent"
                                logger.info(f"🎯 DETECTED: Summarization mode from S3 outputs (file age: {file_age_seconds:.0f}s)")
                                agent_status_display.success("✅ **Agent Assigned:** APS Summarization Agent")
                                add_message("📊 Routed to: APS Summarization Agent (6-step medical analysis)")
                                
                                # Initialize pipeline tracking
                                st.session_state.step_status[0] = "success"  # Step 1 already completed
                                st.session_state.current_step = 0
                                add_message("✅ OCR & Text Extraction completed")
                                tool_display.success("✅ Completed: **OCR & Text Extraction**")
                                
                                # Show initial progress
                                with progress_container.container():
                                    display_pipeline_progress(0)
                            else:
                                logger.info(f"S3 file found but too old ({file_age_seconds:.0f}s) - skipping proactive detection")
                        except:
                            pass  # File doesn't exist yet, continue waiting
                    
                    # Periodically validate S3 outputs (every 5 events to catch files quickly)
                    # Run when: detected summarization mode (from tools OR S3 proactive check)
                    # Skip if: Managing Agent handling conversationally
                    if (event_count % 5 == 0 and is_summarization and 
                        st.session_state.get("selected_agent") != "APS Managing Agent"):
                        logger.info(f"Periodic S3 validation check (event {event_count})...")
                        
                        # Special case: Check if Step 3 completed and no images exist (should skip 4-5)
                        if (st.session_state.step_status[2] == "success" and 
                            st.session_state.step_status[3] == "pending" and
                            st.session_state.step_status[4] == "pending"):
                            # Check if medical_images folder exists in S3
                            try:
                                images_prefix = f"{st.session_state.session_id}/outputs/medical_images/"
                                images_response = s3_client.list_objects_v2(
                                    Bucket=BUCKET_NAME,
                                    Prefix=images_prefix,
                                    MaxKeys=1
                                )
                                has_images = 'Contents' in images_response and len(images_response['Contents']) > 0
                                
                                if not has_images:
                                    # No images found - skip steps 4 and 5, jump to step 6
                                    logger.info("No images found - skipping Steps 4 and 5")
                                    st.session_state.step_status[3] = "skipped"  # Step 4
                                    st.session_state.step_status[4] = "skipped"  # Step 5
                                    st.session_state.step_status[5] = "running"  # Step 6
                                    st.session_state.current_step = 5
                                    
                                    add_message("⏭️ No diagnostic images - skipping Steps 4 & 5")
                                    add_message("🔧 Running: Risk Assessment & Summary")
                                    tool_display.info("🔧 Processing: **Risk Assessment & Summary** (Steps 4-5 skipped)")
                                    
                                    # Update progress display
                                    with progress_container.container():
                                        display_pipeline_progress(5)
                            except Exception as e:
                                logger.warning(f"Error checking for images: {e}")
                    
                    # Check ALL steps to catch files that appear without trace events
                    # Run if: (1) received response chunks OR (2) detected summarization mode from S3
                    # Skip if: Managing Agent handling conversationally
                    if ((has_received_response or is_summarization) and 
                        st.session_state.get("selected_agent") != "APS Managing Agent"):
                        for check_idx in range(len(PIPELINE_STEPS)):
                            # Skip already completed or skipped steps
                            if st.session_state.step_status[check_idx] in ["success", "skipped"]:
                                continue
                            
                            # Quick single check for this step (no retries)
                            try:
                                step_files = [
                                    f"{st.session_state.session_id}/outputs/extracted_text.json",
                                    f"{st.session_state.session_id}/outputs/medical_summary.json",
                                    f"{st.session_state.session_id}/outputs/coded_conditions.json",
                                    f"{st.session_state.session_id}/outputs/diagnostic_tests.json",
                                    f"{st.session_state.session_id}/outputs/diagnostic_summaries.json",
                                    f"{st.session_state.session_id}/outputs/enhanced_medical_summary_with_risks.json"
                                ]
                                if check_idx < len(step_files):
                                    file_key = step_files[check_idx]
                                    s3_client.head_object(Bucket=BUCKET_NAME, Key=file_key)
                                    
                                    # File exists! This confirms we're in summarization mode
                                    if not is_summarization:
                                        is_summarization = True
                                        st.session_state.tools_invoked = True
                                        logger.info("DETECTED: Summarization mode from S3 outputs - initializing pipeline tracking")
                                        agent_status_display.success("✅ **Current Agent:** Summarization Agent (running 6-step medical analysis)")
                                        tool_display.info("🔧 Detected: **6-Step Medical Analysis Pipeline**")
                                    
                                    # File exists! Mark as success and update progress
                                    st.session_state.step_status[check_idx] = "success"
                                    step_name = PIPELINE_STEPS[check_idx]['name']
                                    add_message(f"✅ {step_name} completed")
                                    tool_display.success(f"✅ Completed: **{step_name}**")
                                    logger.info(f"Periodic check: Step {check_idx + 1} output found!")
                                    
                                    # Update current_step to this step
                                    st.session_state.current_step = check_idx
                                    
                                    # Update progress display
                                    with progress_container.container():
                                        display_pipeline_progress(check_idx)
                                    
                                    # If next step exists and is pending, mark it as running
                                    if check_idx + 1 < len(PIPELINE_STEPS):
                                        if st.session_state.step_status[check_idx + 1] == "pending":
                                            st.session_state.step_status[check_idx + 1] = "running"
                                            st.session_state.current_step = check_idx + 1
                                            next_step_name = PIPELINE_STEPS[check_idx + 1]['name']
                                            add_message(f"🔧 Running: {next_step_name}")
                                            tool_display.info(f"🔧 Processing: **{next_step_name}**")
                                            logger.info(f"Periodic check: Marked Step {check_idx + 2} as running")
                                            
                                            # Update progress display
                                            with progress_container.container():
                                                display_pipeline_progress(check_idx + 1)
                            except:
                                pass  # File not found yet, continue checking other steps
                    
                    # Handle chunk events (agent responses)
                    if "chunk" in event:
                        chunk_text = event["chunk"]["bytes"].decode("utf-8")
                        agent_response_text += chunk_text
                        has_received_response = True  # Mark that we've received agent response
                        
                        # If we've received response chunks but no tools invoked yet, it's conversational mode
                        if not is_summarization and not st.session_state.get("selected_agent"):
                            st.session_state.selected_agent = "APS Managing Agent"
                            logger.info("DETECTED: Conversational mode - Managing Agent responding directly")
                        
                        # Only log first 100 characters to avoid terminal spam
                        logger.info(f"Agent response: {chunk_text[:100]}...")
                        
                        # Detect ACTUAL errors in agent response (not just mentions of errors in summaries)
                        # Only flag as error if it's a service/API error, not descriptive text
                        chunk_lower = chunk_text.lower()
                        
                        # Detect Textract/extraction errors - these indicate summarization mode was attempted
                        # IMPORTANT: Only detect ACTUAL errors, not conversational references to past errors
                        # Real errors use definitive language: "could not be completed", "failed", "error occurred"
                        # Conversational references use conditional language: "may have", "when the", "if the"
                        is_textract_error = False
                        if "textract" in chunk_lower or "document extraction" in chunk_lower or "text extraction" in chunk_lower:
                            # Check for definitive error language (not conversational mentions)
                            if ("could not be completed" in chunk_lower or 
                                "cannot be completed" in chunk_lower or
                                "failed" in chunk_lower or
                                "error occurred" in chunk_lower):
                                is_textract_error = True
                            # Exclude conversational references to past errors
                            elif ("may have resolved" in chunk_lower or 
                                  "when the capacity" in chunk_lower or
                                  "if the service" in chunk_lower or
                                  "try again when" in chunk_lower):
                                is_textract_error = False
                        
                        if is_textract_error:
                            # This is a Textract capacity error - indicates summarization was attempted
                            if not is_summarization:
                                is_summarization = True
                                st.session_state.tools_invoked = True
                                logger.info("DETECTED: Summarization mode from Textract error - pipeline was attempted but failed early")
                                agent_status_display.success("✅ **Current Agent:** Summarization Agent (pipeline attempted - Textract capacity error)")
                                # Initialize pipeline tracking
                                st.session_state.step_status[0] = "failed"
                                st.session_state.current_step = 0
                                with progress_container.container():
                                    display_pipeline_progress(0)
                                tool_display.error("❌ **Step 1 Failed:** Document extraction service capacity limit reached")
                            
                            st.session_state.agent_error = chunk_text
                            has_error = True
                            logger.error(f"Textract capacity error: {chunk_text}")
                            add_message("⚠️ AWS Textract capacity limit - please retry in a few minutes", level="error")
                        
                        # Detect other actual errors
                        is_actual_error = (
                            ("error" in chunk_lower and ("exception" in chunk_lower or "failed to" in chunk_lower or "unable to" in chunk_lower)) or
                            "missing required parameter" in chunk_lower or
                            "validationexception" in chunk_lower or
                            "accessdeniedexception" in chunk_lower
                        )
                        
                        if is_actual_error and not has_error:  # Don't overwrite Textract error
                            st.session_state.agent_error = chunk_text
                            has_error = True
                            logger.error(f"Agent error: {chunk_text}")
                            
                            # Pipeline-specific error handling only if in summarization mode
                            if is_summarization:
                                # Mark current step as failed
                                if st.session_state.current_step < len(PIPELINE_STEPS):
                                    st.session_state.step_status[st.session_state.current_step] = "failed"
                                tool_display.error(f"❌ Processing error detected")
                                
                                # Check for specific error types to provide user-friendly messages
                                chunk_lower = chunk_text.lower()
                                if ("textract" in chunk_lower or "document extraction" in chunk_lower) and ("throughput" in chunk_lower or "limit" in chunk_lower or "provisioned" in chunk_lower or "cannot be completed" in chunk_lower):
                                    # AWS Textract service limit error
                                    error_msg = "⚠️ **AWS Textract Service Limit Reached**\n\n" \
                                               "The document extraction service has reached its request limit. This is a temporary issue.\n\n" \
                                               "**Please try again in a few minutes.** The service quota will reset automatically."
                                    add_message(error_msg, level="error")
                                    tool_display.warning("⏳ Service limit reached - please retry in a few minutes")
                                elif "throughput exceeded" in chunk_lower or "provisioned throughput" in chunk_lower:
                                    # Generic throughput error
                                    error_msg = "⚠️ **Service Throughput Limit Exceeded**\n\n" \
                                               "The system is currently experiencing high demand. Please wait a few minutes and try again."
                                    add_message(error_msg, level="error")
                                    tool_display.warning("⏳ High demand - please retry shortly")
                                else:
                                    # Generic error
                                    add_message(f"❌ Error: {chunk_text[:150]}", level="error")
                                
                                # Update progress display to show failure
                                with progress_container.container():
                                    display_pipeline_progress(st.session_state.current_step)
                    
                    # Handle trace events (tool usage, orchestration)
                    # Detect if we're in summarization mode based on tool invocations
                    elif "trace" in event:
                        trace = event["trace"]
                        
                        # Show model invocation (agent thinking)
                        if "modelInvocationInput" in trace:
                            add_message("🧠 Agent: Reasoning and planning...")
                        
                        # Check for agent step information
                        if "agentStep" in trace:
                            agent_step = trace["agentStep"]
                            
                            # Pre-processing
                            if "preProcessing" in agent_step:
                                logger.info("Agent pre-processing request...")
                            
                            # Orchestration (thinking/planning)
                            elif "orchestration" in agent_step:
                                orch = agent_step["orchestration"]
                                if "actionGroupInvocation" in orch:
                                    logger.info("Agent planning action...")
                                else:
                                    logger.info("Agent thinking...")
                            
                            # Tool invocation - start of tool execution
                            elif "toolInvocations" in agent_step:
                                for tool_inv in agent_step["toolInvocations"]:
                                    tool_name = tool_inv.get("toolName", "Unknown")
                                    st.session_state.current_tool = tool_name
                                    
                                    # Detect summarization mode by checking tool names
                                    if not is_summarization and any(tool in tool_name.lower() for tool in ["extraction", "medical", "icd", "diagnostic", "risk"]):
                                        is_summarization = True
                                        st.session_state.tools_invoked = True
                                        st.session_state.selected_agent = "APS Summarization Agent"
                                        logger.info("DETECTED: Summarization mode - initializing 6-step pipeline tracking")
                                        agent_status_display.success("✅ **Agent Assigned:** APS Summarization Agent")
                                        
                                        # Add system message to chat
                                        add_message("📊 Routed to: APS Summarization Agent (6-step medical analysis)")
                                        
                                        # Initialize pipeline tracking
                                        st.session_state.step_status[0] = "running"
                                        st.session_state.current_step = 0
                                        # Show initial progress
                                        with progress_container.container():
                                            display_pipeline_progress(0)
                                    
                                    # Update step based on tool name using mapping (only if summarization)
                                    if is_summarization:
                                        tool_lower = tool_name.lower()
                                        step_found = False
                                        for key, val in STEP_MAP.items():
                                            if key in tool_lower:
                                                step_idx = val - 1  # Convert to 0-indexed
                                            
                                            # Mark previous step as success if we're moving to next step
                                            if st.session_state.current_step < step_idx and st.session_state.current_step < len(PIPELINE_STEPS):
                                                if st.session_state.step_status[st.session_state.current_step] == "running":
                                                    st.session_state.step_status[st.session_state.current_step] = "success"
                                            
                                            # Set current step as running
                                            if step_idx < len(PIPELINE_STEPS):
                                                st.session_state.step_status[step_idx] = "running"
                                                st.session_state.current_step = step_idx
                                            
                                            step_found = True
                                            break
                                        
                                        logger.info(f"Starting tool: {tool_name}")
                                        add_message(f"🔧 Running: {PIPELINE_STEPS[st.session_state.current_step]['name']}")
                                        tool_display.info(f"🔧 Processing: **{PIPELINE_STEPS[st.session_state.current_step]['name']}**")
                                        
                                        # Update progress display immediately
                                        with progress_container.container():
                                            display_pipeline_progress(st.session_state.current_step)
                            
                            # Observation - tool completed
                            elif "observation" in agent_step:
                                obs = agent_step["observation"]
                                
                                # Only process observations if we detected summarization mode
                                if is_summarization:
                                    logger.info(f"Observation event received for step {st.session_state.current_step + 1}")
                                
                                # When we see an observation, the current tool completed
                                if st.session_state.current_step < len(PIPELINE_STEPS):
                                    if st.session_state.step_status[st.session_state.current_step] == "running":
                                        step_name = PIPELINE_STEPS[st.session_state.current_step]['name']
                                        logger.info(f"Tool completed: {step_name}")
                                        
                                        # Check if observation contains error
                                        obs_text = str(obs)
                                        if "error" in obs_text.lower() and "failed" in obs_text.lower():
                                            st.session_state.step_status[st.session_state.current_step] = "failed"
                                            add_message(f"❌ {step_name} failed", level="error")
                                            tool_display.error(f"❌ Failed: **{step_name}**")
                                        else:
                                            # Validate S3 output with retries - this will update status if found
                                            add_message(f"🔍 Verifying {step_name} output...")
                                            tool_display.info(f"🔍 Verifying: **{step_name}**")
                                            
                                            # Validate with retries (max 5 attempts, 3 seconds each)
                                            if validate_single_step_from_s3(st.session_state.session_id, st.session_state.current_step):
                                                add_message(f"✅ {step_name} completed")
                                                tool_display.success(f"✅ Completed: **{step_name}**")
                                            else:
                                                # Mark as success even if S3 validation failed (eventual consistency)
                                                st.session_state.step_status[st.session_state.current_step] = "success"
                                                add_message(f"✅ {step_name} completed (file pending)", level="warning")
                                                tool_display.success(f"✅ Completed: **{step_name}**")
                                        
                                        # Update progress display
                                        with progress_container.container():
                                            display_pipeline_progress(st.session_state.current_step)
                            
                            # Post-processing
                            elif "postProcessing" in agent_step:
                                logger.info("Agent post-processing results...")
                                # Mark current step as success when post-processing
                                if st.session_state.current_step < len(PIPELINE_STEPS):
                                    if st.session_state.step_status[st.session_state.current_step] == "running":
                                        st.session_state.step_status[st.session_state.current_step] = "success"
                                        
                                        # Update progress display
                                        with progress_container.container():
                                            display_pipeline_progress(st.session_state.current_step)
                    
                    # Stop processing if error detected
                    if has_error:
                        logger.error("Stopping pipeline due to error")
                        if is_summarization:
                            tool_display.error("❌ Processing stopped due to error")
                        break
                    
                    # Very small delay to allow UI updates to propagate
                    time.sleep(0.05)
                
                # Event stream completed
                logger.info(f"Event stream iteration completed - Total events: {event_count}")
                logger.info(f"Conversational mode: {not is_summarization} | Selected agent: {st.session_state.get('selected_agent')}")
                
                # Validate step status from S3 outputs (only for summarize mode)
                if is_summarization:
                    logger.info("Validating pipeline outputs from S3...")
                    validate_pipeline_status_from_s3(st.session_state.session_id)
                    
                    # Update progress display with validated status
                    with progress_container.container():
                        display_pipeline_progress(st.session_state.current_step)
                else:
                    # Conversational mode - Managing Agent responded directly without routing
                    logger.info("Conversational mode: Managing Agent handled request directly (no tools invoked)")
                    st.session_state.selected_agent = "APS Managing Agent"
                    agent_status_display.success("✅ **Agent Response:** Managing Agent (conversational)")
                    add_message("💬 Direct response from Managing Agent")
                
                # Check if we stopped due to error
                if has_error:
                    add_message("❌ Processing completed with errors", level="error")
                    logger.error("Agent processing completed with errors")
                    
                    # Add error message to chat history
                    error_msg = st.session_state.agent_error[:300] if st.session_state.agent_error else "An error occurred during processing."
                    st.session_state.chat_messages.append({
                        "role": "assistant",
                        "content": f"⚠️ I encountered an error while processing your request:\n\n{error_msg}\n\nPlease try again or rephrase your question.",
                        "agent": st.session_state.selected_agent or "System",
                        "timestamp": datetime.now()
                    })
                else:
                    add_message("✅ Processing complete!")
                    if is_summarization:
                        logger.info("Summarization mode: Agent processing completed successfully")
                        
                        # For summarization: Add brief confirmation message, not full response
                        # Extract patient name from response if available (simple extraction)
                        patient_name = "the patient"
                        if agent_response_text:
                            # Try to extract name from response (e.g., "for Daniel Robert Kline")
                            import re
                            name_match = re.search(r'for ([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?)', agent_response_text)
                            if name_match:
                                patient_name = name_match.group(1)
                        
                        # Build enhanced status message with risk level if available
                        doc_status_msg = f"✅ APS analysis complete for **{patient_name}**."
                        
                        # Try to add risk level summary if data already loaded
                        if st.session_state.final_data:
                            exec_summary = st.session_state.final_data.get("executive_summary", {})
                            risk_level = exec_summary.get("risk_level", "")
                            if risk_level:
                                risk_emoji = "🔴" if risk_level.lower() == "high" else ("🟡" if risk_level.lower() == "moderate" else "🟢")
                                doc_status_msg += f" Overall risk: {risk_emoji} **{risk_level.capitalize()}**."
                        
                        doc_status_msg += " Click **View Dashboard** below to explore the full results."
                        
                        # Add brief confirmation to chat
                        st.session_state.chat_messages.append({
                            "role": "assistant",
                            "content": doc_status_msg,
                            "agent": st.session_state.selected_agent or "APS Summarization Agent",
                            "timestamp": datetime.now()
                        })
                    else:
                        logger.info("Agent response received successfully")
                        logger.info(f"Agent response length: {len(agent_response_text)} characters")
                        
                        # For conversational mode: Add full response to chat history
                        if agent_response_text:
                            # Extract agent name from response if present (e.g., "APS Processing Agent Response")
                            agent_name = st.session_state.selected_agent or "APS Managing Agent"
                            if "APS Processing Agent" in agent_response_text or "Processing Agent" in agent_response_text:
                                agent_name = "APS Processing Agent"
                            elif "APS Summarization Agent" in agent_response_text or "Summarization Agent" in agent_response_text:
                                agent_name = "APS Summarization Agent"
                            
                            st.session_state.chat_messages.append({
                                "role": "assistant",
                                "content": agent_response_text,
                                "agent": agent_name,
                                "timestamp": datetime.now()
                            })
                
                # Store agent response text for later display (especially for process mode)
                st.session_state.agent_response_text = agent_response_text
                st.session_state.agent_completed = True
                
                time.sleep(2)
                st.rerun()
                
            except Exception as e:
                error_msg = str(e)
                st.error(f"❌ Processing error occurred")
                add_message(f"❌ Error: {error_msg[:150]}", level="error")
                logger.error(f"Agent streaming error: {error_msg}")
                
                # Mark as error and store error message
                st.session_state.agent_error = error_msg
                st.session_state.agent_response_text = agent_response_text  # Store partial response
                
                # Detect if this was a summarization request from error message
                # If error mentions summarization agent CGOEAHZZTT or indicates timeout during processing
                error_lower = error_msg.lower()
                if ("CGOEAHZZTT" in error_msg or  # Summarization agent ID
                    "timed out when processing" in error_msg or  # Timeout during request
                    ("timed out" in error_lower and "agent" in error_lower)):  # General agent timeout
                    logger.info("DETECTED: Summarization mode from error (agent timeout/failure)")
                    is_summarization = True
                    if not st.session_state.selected_agent:
                        st.session_state.selected_agent = "APS Summarization Agent"
                
                # Still validate what was completed before the error
                # Only if we detected summarization mode - simple responses have no pipeline
                if is_summarization:
                    logger.info("Validating completed outputs from S3...")
                    validate_pipeline_status_from_s3(st.session_state.session_id)
                    
                    # Try to load results from S3 to check if we have usable data despite the error
                    temp_final_data = load_final_summary(st.session_state.session_id)
                    
                    if temp_final_data:
                        # Success! We have data from S3 (Steps 1-5 completed)
                        # Don't show error - show success message instead
                        logger.info("✅ Despite timeout, successfully recovered data from S3 (Steps 1-5 complete)")
                        st.session_state.final_data = temp_final_data
                        
                        # Extract patient name for success message
                        patient_name = "the patient"
                        exec_summary = temp_final_data.get("executive_summary", {})
                        if exec_summary.get("patient_name"):
                            patient_name = exec_summary.get("patient_name")
                        
                        # Build success message with risk level
                        success_msg = f"✅ APS analysis complete for **{patient_name}**."
                        risk_level = exec_summary.get("risk_level", "")
                        if risk_level:
                            risk_emoji = "🔴" if risk_level.lower() == "high" else ("🟡" if risk_level.lower() == "moderate" else "🟢")
                            success_msg += f" Overall risk: {risk_emoji} **{risk_level.capitalize()}**."
                        success_msg += " Click **View Dashboard** below to explore the full results."
                        
                        # Add success message to chat (not error)
                        st.session_state.chat_messages.append({
                            "role": "assistant",
                            "content": success_msg,
                            "agent": "APS Summarization Agent",
                            "timestamp": datetime.now()
                        })
                        
                        # Clear error flag since we recovered successfully
                        st.session_state.agent_error = None
                    else:
                        # Genuine failure - no data in S3
                        logger.warning("❌ Timeout AND no data in S3 - genuine failure")
                        st.session_state.chat_messages.append({
                            "role": "assistant",
                            "content": f"❌ An error occurred during processing:\n\n{error_msg[:300]}\n\nPlease try again or contact support if the issue persists.",
                            "agent": st.session_state.selected_agent or "System",
                            "timestamp": datetime.now()
                        })
                    
                    # Update progress display with validated status
                    with progress_container.container():
                        display_pipeline_progress(st.session_state.current_step)
                else:
                    # Non-summarization error - show error message
                    st.session_state.chat_messages.append({
                        "role": "assistant",
                        "content": f"❌ An error occurred during processing:\n\n{error_msg[:300]}\n\nPlease try again or contact support if the issue persists.",
                        "agent": st.session_state.selected_agent or "System",
                        "timestamp": datetime.now()
                    })
                
                # Mark agent as completed so user can see results
                st.session_state.agent_completed = True
                
                # Show concise error message for dependencyFailedException
                if "dependencyFailedException" in error_msg:
                    st.warning("⚠️ **Lambda Error:** A Lambda function returned an error to Bedrock Agent. Check CloudWatch logs for details.")
                    logger.error(f"dependencyFailedException details: {error_msg}")
                
                time.sleep(3)
                st.rerun()
    
    # Stop rendering here to prevent chat input from appearing below
    st.stop()

# =========================================================
# LOADING RESULTS SECTION
# =========================================================
elif st.session_state.agent_completed and not st.session_state.show_dashboard:
    
    st.header("📥 Step 3: Loading Results")
    
    # Check if this was a conversational response (Managing Agent handled directly)
    # Signal: selected_agent is "APS Managing Agent" or agent_response_text exists without errors
    is_conversational = (
        st.session_state.get("selected_agent") == "APS Managing Agent" or
        (st.session_state.agent_response_text and 
         not st.session_state.agent_error and
         st.session_state.get("selected_agent") != "APS Summarization Agent")
    )
    
    if is_conversational:
        # OPTIMIZATION: Only check S3 if tools were actually invoked
        # If no tools invoked (tools_invoked=False), we KNOW it's pure conversational - skip S3 check
        # This prevents wasteful 12+ second S3 checks on simple greetings/questions
        tools_were_invoked = st.session_state.get("tools_invoked", False)
        
        # Only check S3 if both: tools invoked AND no final_data yet
        if tools_were_invoked and st.session_state.final_data is None:
            # Edge case: Managing Agent coordinated with Summarization Agent but returned early
            # Check S3 to see if analysis outputs were created
            logger.info("Conversational mode with tool invocation - checking S3 for summarization outputs")
            
            with st.spinner("⏳ Checking for analysis results..."):
                final_data = load_final_summary(st.session_state.session_id)
                
                if final_data:
                    st.session_state.final_data = final_data
                    logger.info("✅ Found summarization results in S3 - dashboard button will be available")
                else:
                    logger.info("No summarization results found - returning to chat")
        elif st.session_state.final_data is not None:
            logger.info("final_data already loaded — skipping S3 check")
        else:
            # No tools invoked AND no final_data = pure conversational response
            logger.info("No tools invoked - pure conversational response, skipping S3 check entirely")
        
        # Reset agent state to show chat interface with response already in chat history
        st.session_state.agent_running = False
        st.session_state.agent_completed = False
        st.session_state.conversation_active = True
        st.rerun()
    
    # Not conversational - must be summarization or error, validate pipeline from S3
    with st.spinner("⏳ Checking for results..."):
        try:
            validate_pipeline_status_from_s3(st.session_state.session_id)
            # Check if ANY step succeeded OR failed - if so, this was a summarization request
            has_pipeline_attempt = any(status in ["success", "failed", "running"] for status in st.session_state.step_status)
            if has_pipeline_attempt:
                logger.info("Pipeline activity detected - this was a summarization request")
                display_pipeline_progress(6)
            else:
                logger.info("No pipeline activity - checking for errors")
        except:
            has_pipeline_attempt = False
            logger.info("No pipeline outputs detected")
    
    # Check if error message indicates a summarization attempt (Textract/extraction errors)
    is_summarization_error = False
    if st.session_state.agent_error:
        error_lower = st.session_state.agent_error.lower()
        is_summarization_error = ("document extraction" in error_lower or "textract" in error_lower) and \
                                 ("capacity" in error_lower or "throughput" in error_lower or "could not be completed" in error_lower)
    
    # If we detected a summarization error (Textract failure), show error immediately
    if is_summarization_error:
        logger.warning("Textract capacity error detected - showing error message instead of loading results")
        
        st.error("⚠️ **AWS Textract Service Limit Reached**")
        st.markdown("""
        The document extraction service (AWS Textract) has reached its request limit. This is a **temporary** issue.
        
        **What this means:**
        - AWS Textract processes text from PDF documents
        - The service has capacity limits to ensure fair usage
        - Your request was correctly routed to the Summarization Agent, but Step 1 (Text Extraction) failed
        
        **What to do:**
        - ⏰ Wait 2-3 minutes for service capacity to free up
        - 🔄 Click "New Assessment" below and try again
        - ✅ Your PDF is already uploaded and ready
        
        **Note:** This is an AWS infrastructure limitation, not an issue with your document or the application.
        """)
        
        # Show pipeline progress with Step 1 failed
        if has_pipeline_attempt:
            st.markdown("---")
            display_pipeline_progress(0)
        
        st.markdown("---")
        if st.button("🔄 New Assessment", type="primary", use_container_width=True):
            reset_session()
            st.rerun()
        
        st.stop()
    
    st.markdown("---")
    
    # Load summarization results (only for pipeline mode, and only if not already loaded)
    if st.session_state.final_data is None:
        with st.spinner("⏳ Loading final results..."):
            final_data = load_final_summary(st.session_state.session_id)
        
        if final_data:
            st.session_state.final_data = final_data
            logger.info("Final summary loaded successfully")
            
            # Return to chat interface to show completion message
            st.session_state.agent_running = False
            st.session_state.agent_completed = False
            st.session_state.conversation_active = True
            st.rerun()
        else:
            # Final summary not found - show simplified diagnostics
            logger.warning("Final summary not found in S3")
            
            # Check S3 for files created
            s3_files = get_s3_diagnostics(st.session_state.session_id)
            files_created = [f['File'] for f in s3_files] if s3_files else []
            
            # Log detailed diagnostic info to terminal
            logger.info(f"Files created: {files_created}")
            if st.session_state.agent_error:
                logger.error(f"Agent error: {st.session_state.agent_error}")
            
            # Check for Textract/throughput errors first
            if st.session_state.agent_error:
                error_text = st.session_state.agent_error.lower()
                if ("textract" in error_text or "document extraction" in error_text) and ("constrai" in error_text or "constrai" in error_text or "throughput" in error_text or "limit" in error_text or "provisioned" in error_text or "cannot be completed" in error_text):
                    st.error("⚠️ **AWS Textract Service Limit Reached**")
                    st.markdown("""
                    The document extraction service (AWS Textract) has reached its request limit. This is a **temporary** issue.
                    
                    **What this means:**
                    - AWS Textract has a limit on how many requests can be processed per minute
                    - The limit has been temporarily exceeded
                    - This is common during high-usage periods
                    
                    **What to do:**
                    - Wait 2-3 minutes for the quota to reset
                    - Click the "Clear Session & Retry" button below
                    - Upload your document again
                    
                    The service will automatically recover once the rate limit resets.
                    """)
                    
                    if st.button("🔄 Clear Session & Retry", type="primary"):
                        # Clear session
                        for key in list(st.session_state.keys()):
                            del st.session_state[key]
                        st.rerun()
                    
                    st.caption("💡 If the issue persists after multiple retries, please contact support.")
                    logger.error("Textract throughput error displayed to user")
                
                elif "throughput exceeded" in error_text or "provisioned throughput" in error_text:
                    st.error("⚠️ **Service Throughput Limit Exceeded**")
                    st.markdown("""
                    The system is experiencing high demand and has temporarily exceeded its processing capacity.
                    
                    **Please wait 2-3 minutes and try again.**
                    """)
                    
                    if st.button("🔄 Clear Session & Retry", type="primary"):
                        # Clear session
                        for key in list(st.session_state.keys()):
                            del st.session_state[key]
                        st.rerun()
                    
                    logger.error("Throughput error displayed to user")
            
            # Check if this is the "no images" case (Steps 1-3 completed, but 4-5 missing)
            has_step1 = 'extracted_text.json' in files_created
            has_step2 = 'medical_summary.json' in files_created
            has_step3 = 'coded_conditions.json' in files_created
            has_step4 = 'diagnostic_tests.json' in files_created
            has_step5 = 'diagnostic_summaries.json' in files_created
            has_images = any('medical_images' in f.get('Path', '') for f in s3_files)
            
            # Case 1: No images found (valid case - steps 4-5 should be skipped)
            if has_step1 and has_step2 and has_step3 and not has_images and not has_step4:
                st.info("ℹ️ **PDF contains no diagnostic images**")
                st.markdown("""
                This PDF does not contain test results as images. Steps 4 (Diagnostic Test Detection) 
                and 5 (Diagnostic Analysis) have been skipped.
                
                The pipeline should continue to Step 6 (Risk Assessment & Summary) using results from Steps 1-3.
                """)
                st.caption("💡 This is a valid scenario - not all APSs contain test result images.")
                
                logger.info("No images case detected - Steps 4 and 5 skipped, waiting for Step 6")
            
            # Case 2: Critical step failed
            elif not has_step1:
                st.error("❌ **Step 1 (Text Extraction) failed**")
                st.caption("CloudWatch Log: `/aws/lambda/aps-text-and-image-extractor`")
                logger.error("Step 1 failed: No extracted_text.json found")
            elif not has_step2:
                st.error("❌ **Step 2 (Field Calculation) failed**")
                st.caption("CloudWatch Log: `/aws/lambda/aps-medical-summary-generator`")
                logger.error("Step 2 failed: No medical_summary.json found")
            elif not has_step3:
                st.error("❌ **Step 3 (ICD Coding) failed**")
                st.caption("CloudWatch Log: `/aws/lambda/aps-assign-icd-snomed-codes`")
                logger.error("Step 3 failed: No coded_conditions.json found")
            
            # Case 3: Has images but step 4 or 5 failed
            elif has_images and not has_step4:
                st.error("❌ **Step 4 (Diagnostic Detection) failed**")
                st.caption("Images were extracted but detection failed. CloudWatch Log: `/aws/lambda/aps-id-diagnostic-images`")
                logger.error("Step 4 failed: Images exist but diagnostic_tests.json not found")
            elif has_step4 and not has_step5:
                st.error("❌ **Step 5 (Diagnostic Analysis) failed**")
                st.caption("CloudWatch Log: `/aws/lambda/aps-diagnostic-analysis`")
                logger.error("Step 5 failed: No diagnostic_summaries.json found")
            
            # Case 4: Previous steps completed but Step 6 failed
            else:
                # Check if this is a timeout error
                is_timeout_error = st.session_state.agent_error and "timed out" in st.session_state.agent_error.lower()
                
                if is_timeout_error:
                    # Timeout occurred - final summary not generated
                    st.error("⏰ **Analysis Timeout**")
                    st.markdown("""
                    The analysis could not be completed within the time limit. The final risk assessment 
                    and summary (Step 6) require all previous steps to finish successfully.
                    
                    **What happened:**
                    - The Bedrock Agent timed out before completing the full 6-step pipeline
                    - The final summary file (`enhanced_medical_summary_with_risks.json`) was not generated
                    
                    **Next steps:**
                    - Click **Retry Analysis** below to start a new analysis
                    - The increased timeout (20 minutes) should allow more time for processing
                    """)
                    st.caption("💡 Tip: Larger documents with many pages may require more processing time. The timeout has been increased to accommodate this.")
                    
                    if st.button("🔄 Retry Analysis", type="primary", use_container_width=True):
                        # Clean up old S3 outputs before retry
                        logger.info("Cleaning up old S3 outputs from previous failed attempt")
                        try:
                            output_prefix = f"{st.session_state.session_id}/outputs/"
                            delete_response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=output_prefix)
                            if 'Contents' in delete_response:
                                for obj in delete_response['Contents']:
                                    s3_client.delete_object(Bucket=BUCKET_NAME, Key=obj['Key'])
                                    logger.info(f"Deleted old output: {obj['Key']}")
                        except Exception as e:
                            logger.warning(f"Could not clean old S3 outputs: {e}")
                        
                        # Reset and retry
                        st.session_state.agent_completed = False
                        st.session_state.agent_running = False
                        st.session_state.agent_error = None
                        st.session_state.current_step = 0
                        st.session_state.step_status = ["pending"] * len(PIPELINE_STEPS)
                        logger.info("User initiated retry after timeout")
                        st.rerun()
                else:
                    # Regular Step 6 failure (not timeout)
                    st.error("❌ **Step 6 (Risk Assessment & Summary) failed**")
                    st.markdown("""
                    The final risk assessment and summary could not be generated. This is the last step 
                    that combines all previous analysis into the comprehensive dashboard.
                    
                    **Possible causes:**
                    - Lambda function error or timeout
                    - Data validation issues from previous steps
                    - S3 write permissions issue
                    """)
                    st.caption("📋 CloudWatch Log: `/aws/lambda/aps-risk-analyzer-and-summary`")
                    logger.error("Step 6 failed: No enhanced_medical_summary_with_risks.json found")
            
            if st.button("⬅️ Go Back and Try Again"):
                # Reset all agent state including step status to clear failure icons
                st.session_state.agent_completed = False
                st.session_state.agent_running = False
                st.session_state.agent_error = None
                st.session_state.current_step = 0
                st.session_state.step_status = ["pending"] * len(PIPELINE_STEPS)
                st.rerun()
    else:
        # final_data already exists - this shouldn't normally happen for summarization requests
        # but handle gracefully by returning to chat
        logger.info("final_data already loaded in Loading Results section - returning to chat")
        st.session_state.agent_running = False
        st.session_state.agent_completed = False
        st.session_state.conversation_active = True
        st.rerun()

# =========================================================
# DASHBOARD BUTTON SECTION
# =========================================================
elif st.session_state.final_data and not st.session_state.show_dashboard and not st.session_state.conversation_active:
    
    st.header("📥 Step 3: Results Ready")
    
    display_pipeline_progress(6)
    
    st.markdown("---")
    
    st.success("✅ Summary loaded successfully!")
    
    # Show button to generate dashboard
    st.markdown("---")
    if st.button("📊 Generate Dashboard", type="primary", width='stretch'):
        st.session_state.show_dashboard = True
        
        # Load PDF from S3 when dashboard is generated
        with st.spinner("📄 Loading PDF..."):
            try:
                pdf_bytes = load_pdf_from_s3(
                    st.session_state.session_id,
                    BUCKET_NAME,
                    s3_client
                )
                if pdf_bytes:
                    st.session_state.pdf_bytes = pdf_bytes
                    st.session_state.pdf_page = 1  # Start at page 1
                    logger.info("PDF loaded successfully for viewer")
                else:
                    logger.warning("PDF could not be loaded from S3")
                    st.session_state.pdf_bytes = None
            except Exception as e:
                logger.error(f"Error loading PDF: {e}")
                st.session_state.pdf_bytes = None
        
        logger.info("Dashboard button clicked - setting show_dashboard=True")
        st.rerun()
    
    st.info("Click the button above to view the interactive dashboard with patient details, medical history, and diagnostic images.")

# =========================================================
# DISPLAY RESULTS SECTION - DASHBOARD ONLY (NO CHAT)
# =========================================================
elif st.session_state.final_data and st.session_state.show_dashboard:
    
    # Dashboard header with PDF viewer toggle
    col1, col2 = st.columns([8, 2])
    with col1:
        st.header("📊 Medical Analysis Dashboard")
    with col2:
        # PDF Viewer toggle button
        def toggle_pdf_viewer():
            st.session_state.pdf_viewer_visible = not st.session_state.pdf_viewer_visible
        
        viewer_btn_text = "◀ Close Viewer" if st.session_state.pdf_viewer_visible else "📄 Open Viewer"
        st.button(viewer_btn_text, key="toggle_viewer_header", use_container_width=True, on_click=toggle_pdf_viewer)
    
    st.markdown("---")
    
    data = st.session_state.final_data
    section = st.session_state.dashboard_section
    
    # Define columns based on PDF viewer visibility state
    if st.session_state.pdf_viewer_visible:
        dashboard_col, pdf_col = st.columns([7, 3])
    else:
        # If viewer is hidden, dashboard uses full width
        dashboard_col = st.container()
    
    # =========================================================
    # MAIN DASHBOARD CONTENT
    # =========================================================
    with dashboard_col:
        # -------------------- CUSTOMER CARDS -------------------- #
        st.subheader("Patient Overview")
        
        col1, col2, col3, col4 = st.columns(4)
        
        customer_details = data.get("customer_details", {})
        col1.metric("Name", customer_details.get("name", "N/A"))
        col2.metric("Age", customer_details.get("age", "N/A"))
        col3.metric("Sex", customer_details.get("sex", "N/A"))
        col4.metric("PCP", customer_details.get("pcp", "N/A"))
        
        st.markdown("---")
    
        # =========================================================
        # DASHBOARD SECTIONS
        # =========================================================
        # =========================================================
        # SECTION: CUSTOMER DETAILS
        # =========================================================
        if section == "Customer Details":
            
            st.subheader("Customer Details")
            
            # Get bbox lookups for customer details
            bbox_lookups = data.get("bbox_lookups", {})
            overview_boxes = bbox_lookups.get("overview_boxes", {})
            insurance_boxes = bbox_lookups.get("insurance_boxes", {})
            substance_use_boxes = bbox_lookups.get("substance_use_boxes", {})
            
            # Helper function to create 3-column field row (Field | Value | Link)
            def create_field_row(field_name, field_value, boxes_dict, box_key, key_prefix, row_idx):
                """Create a 3-column row: Field Name | Value | Link
                
                Args:
                    field_name: Display name for the field
                    field_value: Value to display
                    boxes_dict: Dictionary containing bbox arrays (e.g., overview_boxes)
                    box_key: Key to look up in boxes_dict (e.g., 'name', 'date_of_birth')
                    key_prefix: Prefix for button key
                    row_idx: Row index for unique key
                """
                col1, col2, col3 = st.columns([2, 4, 1])
                
                with col1:
                    st.write(f"**{field_name}**")
                
                with col2:
                    st.write(field_value)
                
                with col3:
                    # Check if boxes exist for this field
                    if boxes_dict and box_key in boxes_dict:
                        boxes = boxes_dict[box_key]
                        if boxes and len(boxes) > 0:
                            # Extract page and bbox from first box
                            first_box = boxes[0]
                            page = first_box.get("page")
                            bbox = first_box.get("bbox")
                            
                            if page and bbox:
                                # Create evidence dict for navigation
                                evidence_dict = {"evidence": [{"page": page, "bbox": bbox}]}
                                if st.button(f"📄 P.{page}", key=f"{key_prefix}_{box_key}_{row_idx}", type="secondary"):
                                    navigate_to_page_with_condition(page, evidence_dict)
                                    st.rerun()
                            else:
                                st.write("—")
                        else:
                            st.write("—")
                    else:
                        st.write("—")
            
            # Demographics Section
            st.markdown("### 📋 Patient Demographics")
            
            create_field_row("Name", customer_details.get('name', 'N/A'), overview_boxes, "name", "demo", 0)
            create_field_row("Date of Birth", customer_details.get('dob', 'N/A'), overview_boxes, "date_of_birth", "demo", 1)
            create_field_row("Age", customer_details.get('age', 'N/A'), overview_boxes, "age", "demo", 2)
            create_field_row("Sex", customer_details.get('sex', 'N/A'), overview_boxes, "sex", "demo", 3)
            create_field_row("Primary Care Physician", customer_details.get('pcp', 'N/A'), overview_boxes, "pcp", "demo", 4)
            
            if customer_details.get('occupation', 'N/A') != 'N/A':
                create_field_row("Occupation", customer_details.get('occupation', 'N/A'), overview_boxes, "occupation", "demo", 5)
            
            # Contact Information
            st.markdown("")
            st.markdown("### 📞 Contact Information")
            
            contact_info_exists = False
            row_idx = 0
            
            if customer_details.get('contact', 'N/A') != 'N/A':
                create_field_row("Phone", customer_details.get('contact', 'N/A'), overview_boxes, "phone", "contact", row_idx)
                contact_info_exists = True
                row_idx += 1
            
            if customer_details.get('address', 'N/A') != 'N/A':
                create_field_row("Address", customer_details.get('address', 'N/A'), overview_boxes, "address", "contact", row_idx)
                contact_info_exists = True
            
            if not contact_info_exists:
                st.caption("No contact information available")
            
            # Insurance Information
            st.markdown("")
            st.markdown("### 🏥 Insurance Information")
            insurance_provider = customer_details.get('insurance_provider', 'N/A')
            policy_id = customer_details.get('policy_id', 'N/A')
            
            if insurance_provider != 'N/A' or policy_id != 'N/A':
                if insurance_provider != 'N/A':
                    create_field_row("Provider", insurance_provider, insurance_boxes, "provider", "insurance", 0)
                if policy_id != 'N/A':
                    create_field_row("Policy/ID Number", policy_id, insurance_boxes, "ID_number", "insurance", 1)
            else:
                st.caption("No insurance information available")
            
            # Substance Use History
            st.markdown("")
            st.markdown("### 🚬 Substance Use History")
            substance_use = data.get("substance_use", {})
            alcohol = substance_use.get("alcohol", "Not specified")
            tobacco = substance_use.get("tobacco", "Not specified")
            other = substance_use.get("other", "Not specified")
            
            has_substance_use = any(val != "Not specified" for val in [alcohol, tobacco, other])
            
            if has_substance_use:
                row_idx = 0
                if alcohol != "Not specified":
                    create_field_row("Alcohol", alcohol, substance_use_boxes, "alcohol", "substance", row_idx)
                    row_idx += 1
                if tobacco != "Not specified":
                    create_field_row("Tobacco", tobacco, substance_use_boxes, "tobacco", "substance", row_idx)
                    row_idx += 1
                if other != "Not specified":
                    create_field_row("Other", other, substance_use_boxes, "other", "substance", row_idx)
            else:
                st.caption("No substance use history recorded")
            
            # =========================================================
            # SECTION: SUMMARY
            # =========================================================
        elif section == "Summary":
            
            st.subheader("📋 Summary")
            
            # Risk Level Legend/Indicator
            st.markdown(
                """
                <div style="
                    background: #F8F9FA;
                    border: 1px solid #DEE2E6;
                    border-radius: 0.5rem;
                    padding: 0.75rem 1rem;
                    margin-bottom: 1.5rem;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 2rem;
                    flex-wrap: wrap;
                ">
                    <div style="font-weight: 600; color: #495057; font-size: 0.95rem;">Risk Level Indicator:</div>
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        <div style="width: 16px; height: 16px; background: #DC3545; border-radius: 3px;"></div>
                        <span style="color: #495057; font-size: 0.9rem;">High Risk</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        <div style="width: 16px; height: 16px; background: #ED8B00; border-radius: 3px;"></div>
                        <span style="color: #495057; font-size: 0.9rem;">Moderate Risk</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        <div style="width: 16px; height: 16px; background: #86BC25; border-radius: 3px;"></div>
                        <span style="color: #495057; font-size: 0.9rem;">Low Risk</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            
            # Get both narrative summary and key points from executive_summary (directly from Lambda output)
            exec_summary = data.get("executive_summary", {})
            summary_text = exec_summary.get("narrative_summary", "No summary available.")
            key_points = exec_summary.get("key_summary_points", [])
            
            # Display narrative summary with color coding and LLM-generated key points
            # Use narrative_summary_keywords for highlighting
            render_colored_summary(summary_text, show_bullets=True, key_points=key_points, keyword_source="narrative_summary")
            
            # Add bbox link for the summary text if available in health_history_boxes
            bbox_lookups = data.get("bbox_lookups", {})
            health_history_boxes = bbox_lookups.get("health_history_boxes", [])
            
            # if health_history_boxes and len(health_history_boxes) > 0:
            #     # Get page from first health history box
            #     first_box = health_history_boxes[0]
            #     page = first_box.get("page")
            #     bbox = first_box.get("bbox")
                
            #     if page and bbox:
            #         # Create evidence dict from all health_history_boxes on this page
            #         page_boxes = [box for box in health_history_boxes if box.get("page") == page]
            #         bboxes = [box.get("bbox") for box in page_boxes if box.get("bbox")]
                    
                    # if bboxes:
                    #     if st.button(f"📄 View Health History on Page {page}", key="summary_health_history", type="secondary"):
                    #         st.session_state.pdf_page = page
                    #         st.session_state.highlight_bbox = bboxes
                    #         st.rerun()
            
            st.markdown("---")
            st.subheader("⚠️ Risk Assessment")
            
            # Display overall risk assessment from executive_summary with color coding
            # Use risk_assessment_keywords for highlighting (different from narrative_summary)
            overall_risk = exec_summary.get("overall_risk_assessment", "Risk assessment pending completion.")
            render_colored_summary(overall_risk, title='Overall Assessment:', keyword_source="risk_assessment")
            
            # High Risk Analysis - separated by code type
            st.markdown("---")
            st.markdown("### ⚠️ High Risk Analysis")
            st.caption("Critical medical conditions and medications identified through clinical analysis.")
            
            # Get health_concerns from transformed summary data structure
            summary_data = data.get("summary", {})
            health_concerns = summary_data.get("health_concerns", []) or []
            
            if health_concerns and len(health_concerns) > 0:
                # Get bbox lookups for medical history
                bbox_lookups = data.get("bbox_lookups", {})
                medical_history_boxes = bbox_lookups.get("medical_history_boxes", [])
                health_history_boxes = bbox_lookups.get("health_history_boxes", [])
                coded_conditions = data.get("coded_conditions", [])
                
                # Separate by source and code_type - ONLY High Risk items for Summary tab
                # NOTE: Items from high_risk_conditions already have hierarchical metadata (hierarchy_depth, hierarchical_path)
                # so we don't need a separate "hierarchical_snomed" source
                main_snomed_conditions = [c for c in health_concerns if c.get("source") == "main_categorized" and c.get("code_type") == "SNOMED" and c.get("condition_type") == "High Risk"]
                main_rxnorm_medications = [c for c in health_concerns if c.get("source") == "main_categorized" and c.get("code_type") == "RxNorm" and c.get("condition_type") == "High Risk"]
                
                # These sources may be empty due to deduplication - hierarchical metadata is in main_categorized
                hierarchical_snomed = [c for c in health_concerns if c.get("source") == "hierarchical_snomed"]
                hierarchical_rxnorm = [c for c in health_concerns if c.get("source") == "hierarchical_rxnorm"]
                
                # Define shared function for hierarchical grouping (used by both SNOMED and RxNorm)
                def group_hierarchical_for_summary(conditions):
                    """Group conditions by parent code for collapsible display.
                    Creates parent nodes from hierarchical_path if parent doesn't exist in data.
                    
                    Hierarchy depth interpretation:
                    - depth 0: Standalone (no hierarchical relationship)
                    - depth 1+: Child of the root parent in hierarchical_path[0]
                    """
                    groups = {}
                    standalone = []
                    
                    for cond in conditions:
                        hierarchy_depth = cond.get("hierarchy_depth", 0)
                        hierarchical_path = cond.get("hierarchical_path", [])
                        snomed_code = cond.get("snomed_code", "")
                        rxnorm_code = cond.get("rxnorm_code", "")
                        code = snomed_code or rxnorm_code
                        
                        # If no hierarchy info or depth 0, it's standalone
                        if not hierarchical_path or hierarchy_depth == 0:
                            standalone.append(cond)
                            continue
                        
                        # If depth >= 1, it's a CHILD of the root parent in hierarchical_path[0]
                        if hierarchy_depth >= 1 and len(hierarchical_path) > 0:
                            parent_code = hierarchical_path[0][0]  # First node in path is parent
                            parent_info = hierarchical_path[0]  # [code, name]
                            
                            if parent_code not in groups:
                                # Create synthetic parent from hierarchical_path
                                synthetic_parent = {
                                    "title": parent_info[1] if len(parent_info) > 1 else "Unknown",
                                    "condition": parent_info[1] if len(parent_info) > 1 else "Unknown",
                                    "snomed_code": parent_info[0] if cond.get("code_type") == "SNOMED" or not cond.get("code_type") else "",
                                    "rxnorm_code": parent_info[0] if cond.get("code_type") == "RxNorm" else "",
                                    "code_type": cond.get("code_type", "SNOMED"),
                                    "category": cond.get("category", ""),
                                    "hierarchy_depth": 0,
                                    "hierarchical_path": [parent_info],
                                    "path_display": parent_info[1] if len(parent_info) > 1 else "Unknown",
                                    "evidence_boxes": [],  # No evidence for synthetic parent
                                    "has_evidence": False,
                                    "is_synthetic": True  # Mark as synthetic
                                }
                                groups[parent_code] = {"parent": synthetic_parent, "children": [cond]}
                            else:
                                groups[parent_code]["children"].append(cond)
                    
                    return groups, standalone
                
                # Display Main SNOMED Conditions (from main categorized analysis) with Hierarchical Grouping
                if main_snomed_conditions:
                    st.markdown("#### 🩺 High Risk Medical Conditions")
                    st.caption(f"{len(main_snomed_conditions)} critical condition{'s' if len(main_snomed_conditions) > 1 else ''} requiring underwriting attention")
                    
                    # Group conditions hierarchically if they have hierarchical metadata
                    conditions_with_hierarchy = [c for c in main_snomed_conditions if c.get("hierarchy_depth", 0) > 0]
                    conditions_without_hierarchy = [c for c in main_snomed_conditions if c.get("hierarchy_depth",0) == 0]
                    
                    if conditions_with_hierarchy:
                        # Apply hierarchical grouping
                        hierarchical_groups, standalone = group_hierarchical_for_summary(conditions_with_hierarchy)
                        
                        # Display hierarchical groups with collapsible parent-child
                        group_count = 0
                        for parent_code, group in hierarchical_groups.items():
                            parent = group["parent"]
                            children = group["children"]
                            
                            if parent:
                                # Display parent with collapsible children
                                parent_name = parent.get("title") or parent.get("condition") or "Unknown Condition"
                                parent_snomed = parent.get("snomed_code", "")
                                parent_icd10 = parent.get("icd10_code", "")
                                parent_category = parent.get("category", "")
                                parent_page = parent.get("page_reference") or parent.get("page")
                                parent_date = parent.get("date") or parent.get("date_reported", "")
                                parent_evidence_boxes = parent.get("evidence_boxes", [])
                                parent_description = parent.get("description", "")
                                
                                # Parent section with expander for children
                                with st.expander(f"**{parent_name}** ({len(children)} related finding{'s' if len(children) != 1 else ''})", expanded=True):
                                    # Check if this is a synthetic parent (created from hierarchical_path but not in original data)
                                    is_synthetic = parent.get("is_synthetic", False)
                                    
                                    # Parent details
                                    if is_synthetic:
                                        st.markdown(f"**Parent Category:** {parent_name}")
                                        st.caption("ℹ️ Parent category inferred from hierarchical relationship")
                                    else:
                                        st.markdown(f"**Parent Condition:** {parent_name}")
                                    
                                    # Show codes
                                    code_parts = []
                                    if parent_snomed:
                                        code_parts.append(f"SNOMED: {parent_snomed}")
                                    if parent_icd10:
                                        code_parts.append(f"ICD-10: {parent_icd10}")
                                    if code_parts:
                                        st.caption(f"🏥 {' • '.join(code_parts)}")
                                    
                                    # Show category and page (only for non-synthetic parents)
                                    if not is_synthetic:
                                        category_parts = []
                                        if parent_category:
                                            category_parts.append(f"📂 {parent_category}")
                                        if parent_page:
                                            category_parts.append(f"📄 Page {parent_page}")
                                        if parent_date:
                                            category_parts.append(f"📅 {parent_date}")
                                        if category_parts:
                                            st.caption(" • ".join(category_parts))
                                        
                                        # Show parent description
                                        if parent_description:
                                            st.markdown(f"_{parent_description}_")
                                        
                                        # Parent page navigation button
                                        if parent_page and parent_evidence_boxes:
                                            evidence_dict = {"evidence": parent_evidence_boxes}
                                            if st.button(f"📄 View Parent on Page {parent_page}", key=f"main_snomed_parent_{group_count}", type="secondary"):
                                                navigate_to_page_with_condition(parent_page, evidence_dict)
                                                st.rerun()
                                    
                                    st.markdown("---")
                                    
                                    # Display children
                                    if children:
                                        st.markdown(f"**└─ Specific Findings ({len(children)}):**")
                                        for child_idx, child in enumerate(children):
                                            child_name = child.get("title") or child.get("condition") or "Unknown"
                                            child_snomed = child.get("snomed_code", "")
                                            child_icd10 = child.get("icd10_code", "")
                                            child_page = child.get("page_reference") or child.get("page")
                                            child_date = child.get("date") or child.get("date_reported", "")
                                            child_evidence_boxes = child.get("evidence_boxes", [])
                                            child_evidence_text = child.get("evidence") or child.get("original_condition", "")
                                            child_description = child.get("description", "")
                                            
                                            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;**{child_idx + 1}. {child_name}**")
                                            
                                            # Child codes
                                            child_code_parts = []
                                            if child_snomed:
                                                child_code_parts.append(f"SNOMED: {child_snomed}")
                                            if child_icd10:
                                                child_code_parts.append(f"ICD-10: {child_icd10}")
                                            if child_code_parts:
                                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;🏥 {' • '.join(child_code_parts)}")
                                            
                                            # Child metadata
                                            meta_parts = []
                                            if child_page:
                                                meta_parts.append(f"📄 Page {child_page}")
                                            if child_date:
                                                meta_parts.append(f"📅 {child_date}")
                                            if child_evidence_boxes:
                                                meta_parts.append(f"📊 {len(child_evidence_boxes)} evidence boxes")
                                            if meta_parts:
                                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;{' • '.join(meta_parts)}")
                                            
                                            # Show child description
                                            if child_description:
                                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;_{child_description}_")
                                            
                                            # Show evidence text
                                            if child_evidence_text and child_evidence_text.lower() != child_name.lower():
                                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;💬 \\\"{child_evidence_text}\\\"")
                                            
                                            # Child page navigation button
                                            if child_page and child_evidence_boxes:
                                                evidence_dict = {"evidence": child_evidence_boxes}
                                                if st.button(f"📄 View on Page {child_page}", key=f"main_snomed_child_{group_count}_{child_idx}", type="secondary"):
                                                    navigate_to_page_with_condition(child_page, evidence_dict)
                                                    st.rerun()
                                            
                                            if child_idx < len(children) - 1:
                                                st.markdown("&nbsp;")
                                
                                group_count += 1
                            else:
                                # Children without parent - display them normally
                                for child in children:
                                    condition_name_raw = child.get("title") or child.get("condition") or "Unknown"
                                    condition_name = to_camel_title(condition_name_raw)
                                    snomed_code = child.get("snomed_code", "")
                                    icd10_code = child.get("icd10_code", "")
                                    page_ref = child.get("page_reference") or child.get("page")
                                    date_reported = child.get("date") or child.get("date_reported", "")
                                    evidence_boxes = child.get("evidence_boxes", [])
                                    description = child.get("description", "")
                                    evidence_text = child.get("evidence") or child.get("original_condition", "")
                                    
                                    st.markdown(f"**{condition_name}**")
                                    if snomed_code or icd10_code:
                                        code_parts = []
                                        if snomed_code:
                                            code_parts.append(f"SNOMED: {snomed_code}")
                                        if icd10_code:
                                            code_parts.append(f"ICD-10: {icd10_code}")
                                        st.caption(f"🏥 {' • '.join(code_parts)}")
                                    
                                    if description:
                                        st.markdown(f"_{description}_")
                                    
                                    if evidence_text and evidence_text.lower() != condition_name.lower():
                                        st.caption(f"💬 \\\"{evidence_text}\\\"")
                                    
                                    if page_ref and evidence_boxes:
                                        evidence_dict = {"evidence": evidence_boxes}
                                        if st.button(f"📄 Page {page_ref} • 📅 {date_reported}", key=f"main_snomed_child_noparent_{group_count}", type="secondary"):
                                            navigate_to_page_with_condition(page_ref, evidence_dict)
                                            st.rerun()
                                    
                                    st.divider()
                                    group_count += 1
                        
                        # Display standalone conditions from hierarchical set
                        for idx, concern in enumerate(standalone):
                            condition_name_raw = concern.get("title") or concern.get("condition") or "Unknown Condition"
                            condition_name = to_camel_title(condition_name_raw)
                            snomed_code = concern.get("snomed_code")
                            icd10_code = concern.get("icd10_code")
                            page_ref = concern.get("page_reference") or concern.get("page")
                            date_reported = concern.get("date") or concern.get("date_reported", "")
                            evidence_boxes = concern.get("evidence_boxes", [])
                            description = concern.get("description", "")
                            evidence_text = concern.get("evidence") or concern.get("original_condition", "")
                            
                            st.markdown(f"**{condition_name}**")
                            if snomed_code or icd10_code:
                                code_parts = []
                                if snomed_code:
                                    code_parts.append(f"SNOMED: {snomed_code}")
                                if icd10_code:
                                    code_parts.append(f"ICD-10: {icd10_code}")
                                st.caption(f"🏥 {' • '.join(code_parts)}")
                            
                            if description:
                                st.markdown(f"_{description}_")
                            
                            if evidence_text and evidence_text.lower() != condition_name.lower():
                                st.caption(f"💬 \\\"{evidence_text}\\\"")
                            
                            if page_ref and evidence_boxes:
                                evidence_dict = {"evidence": evidence_boxes}
                                if st.button(f"📄 Page {page_ref} • 📅 {date_reported}", key=f"main_snomed_standalone_{idx}", type="secondary"):
                                    navigate_to_page_with_condition(page_ref, evidence_dict)
                                    st.rerun()
                            
                            st.divider()
                    
                    # Display conditions without hierarchical metadata (flat display)
                    if conditions_without_hierarchy:
                        # Sort by date (most recent first)
                        snomed_sorted = sorted(
                            conditions_without_hierarchy,
                            key=lambda x: x.get("date") or x.get("date_reported") or "1900-01-01",
                            reverse=True
                        )
                        
                        for idx, concern in enumerate(snomed_sorted):
                            condition_name_raw = concern.get("title") or concern.get("condition") or "Unknown Condition"
                            condition_name = to_camel_title(condition_name_raw)
                            description = concern.get("description") or ""
                            date_reported = concern.get("date") or concern.get("date_reported") or ""
                            page_ref = concern.get("page_reference") or concern.get("page")
                            category = concern.get("category", "")
                            evidence = concern.get("evidence") or concern.get("original_condition") or ""
                            snomed_code = concern.get("snomed_code")
                            icd10_code = concern.get("icd10_code")
                            
                            with st.container():
                                st.markdown(f"**{idx + 1}. {condition_name}**")
                                
                                # Show medical codes (SNOMED, ICD-10)
                                code_parts = []
                                if snomed_code:
                                    code_parts.append(f"SNOMED: {snomed_code}")
                                if icd10_code:
                                    code_parts.append(f"ICD-10: {icd10_code}")
                                if code_parts:
                                    st.caption(f"🏥 {' • '.join(code_parts)}")
                                
                                # Show category and page link inline
                                category_parts = []
                                if category:
                                    category_parts.append(f"📂 {category}")
                                if page_ref:
                                    category_parts.append(f"📄 Page {page_ref}")
                                if category_parts:
                                    category_line = " • ".join(category_parts)
                                    # Priority 1: Use evidence_boxes directly from backend (already enriched)
                                    evidence_boxes = concern.get("evidence_boxes", [])
                                    
                                    if evidence_boxes:
                                        # Create evidence dict directly from bbox array
                                        evidence_dict = {"evidence": evidence_boxes}
                                        if st.button(category_line, key=f"main_snomed_cond_flat_{idx}_eb", type="secondary", width='content'):
                                            navigate_to_page_with_condition(page_ref, evidence_dict)
                                            st.rerun()
                                    else:
                                        # Priority 2: Fallback to merge from both sources
                                        search_text = evidence or condition_name
                                        merged_bboxes = merge_bbox_from_both_sources(
                                            search_text,
                                            coded_conditions,
                                            medical_history_boxes + health_history_boxes
                                        )
                                        
                                        if merged_bboxes:
                                            evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                                            if st.button(category_line, key=f"main_snomed_cond_flat_{idx}_mb", type="secondary", width='content'):
                                                navigate_to_page_with_condition(page_ref, evidence_dict)
                                                st.rerun()
                                        else:
                                            # Priority 3: Page-only navigation (no highlighting)
                                            if st.button(category_line, key=f"main_snomed_cond_flat_{idx}_nb", type="secondary", width='content'):
                                                st.session_state.pdf_page = page_ref
                                                st.session_state.highlight_bbox = None
                                                st.rerun()
                                
                                # Show clinical summary
                                if description:
                                    st.markdown(f"_{description}_")
                                
                                # Show evidence if different from condition name
                                if evidence and evidence.lower() != condition_name.lower():
                                    st.caption(f"💬 Document text: \"{evidence}\"")
                                
                                # Show date
                                if date_reported:
                                    st.caption(f"📅 {date_reported}")
                                
                                st.divider()
                
                # Display Main RxNorm Medications (from main categorized analysis) with Hierarchical Grouping
                if main_rxnorm_medications:
                    st.markdown("")
                    st.markdown("#### 💊 High Risk Medications")
                    st.caption(f"{len(main_rxnorm_medications)} high-risk medication{'s' if len(main_rxnorm_medications) > 1 else ''} requiring monitoring")
                    
                    # Group medications hierarchically if they have hierarchical metadata
                    medications_with_hierarchy = [m for m in main_rxnorm_medications if m.get("hierarchy_depth", 0) > 0]
                    medications_without_hierarchy = [m for m in main_rxnorm_medications if m.get("hierarchy_depth", 0) == 0]
                    
                    if medications_with_hierarchy:
                        # Apply hierarchical grouping
                        hierarchical_groups, standalone = group_hierarchical_for_summary(medications_with_hierarchy)
                        
                        # Display hierarchical groups with collapsible parent-child
                        group_count = 0
                        for parent_code, group in hierarchical_groups.items():
                            parent = group["parent"]
                            children = group["children"]
                            
                            if parent:
                                # Display parent with collapsible children
                                parent_name = parent.get("title") or parent.get("condition") or "Unknown Medication"
                                parent_rxnorm = parent.get("rxnorm_code", "")
                                parent_icd10 = parent.get("icd10_code", "")
                                parent_category = parent.get("category", "")
                                parent_page = parent.get("page_reference") or parent.get("page")
                                parent_date = parent.get("date") or parent.get("date_reported", "")
                                parent_evidence_boxes = parent.get("evidence_boxes", [])
                                parent_description = parent.get("description", "")
                                
                                # Parent section with expander for children
                                with st.expander(f"**{parent_name}** ({len(children)} specific formulation{'s' if len(children) != 1 else ''})", expanded=True):
                                    # Check if this is a synthetic parent (created from hierarchical_path but not in original data)
                                    is_synthetic = parent.get("is_synthetic", False)
                                    
                                    # Parent details
                                    if is_synthetic:
                                        st.markdown(f"**Parent Category:** {parent_name}")
                                        st.caption("ℹ️ Parent category inferred from hierarchical relationship")
                                    else:
                                        st.markdown(f"**Parent Medication:** {parent_name}")
                                    
                                    # Show codes
                                    code_parts = []
                                    if parent_rxnorm:
                                        code_parts.append(f"RxNorm: {parent_rxnorm}")
                                    if parent_icd10:
                                        code_parts.append(f"ICD-10: {parent_icd10}")
                                    if code_parts:
                                        st.caption(f"💊 {' • '.join(code_parts)}")
                                    
                                    # Show category and page (only for non-synthetic parents)
                                    if not is_synthetic:
                                        category_parts = []
                                        if parent_category:
                                            category_parts.append(f"📂 {parent_category}")
                                        if parent_page:
                                            category_parts.append(f"📄 Page {parent_page}")
                                        if parent_date:
                                            category_parts.append(f"📅 {parent_date}")
                                        if category_parts:
                                            st.caption(" • ".join(category_parts))
                                        
                                        # Show parent description
                                        if parent_description:
                                            st.markdown(f"_{parent_description}_")
                                        
                                        # Parent page navigation button
                                        if parent_page and parent_evidence_boxes:
                                            evidence_dict = {"evidence": parent_evidence_boxes}
                                            if st.button(f"📄 View Parent on Page {parent_page}", key=f"main_rxnorm_parent_{group_count}", type="secondary"):
                                                navigate_to_page_with_condition(parent_page, evidence_dict)
                                                st.rerun()
                                    
                                    
                                    # Display children
                                    if children:
                                        st.markdown(f"**└─ Specific Formulations ({len(children)}):**")
                                        for child_idx, child in enumerate(children):
                                            child_name = child.get("title") or child.get("condition") or "Unknown"
                                            child_rxnorm = child.get("rxnorm_code", "")
                                            child_icd10 = child.get("icd10_code", "")
                                            child_page = child.get("page_reference") or child.get("page")
                                            child_date = child.get("date") or child.get("date_reported", "")
                                            child_evidence_boxes = child.get("evidence_boxes", [])
                                            child_evidence_text = child.get("evidence") or child.get("original_condition", "")
                                            child_description = child.get("description", "")
                                            
                                            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;**{child_idx + 1}. {child_name}**")
                                            
                                            # Child codes
                                            child_code_parts = []
                                            if child_rxnorm:
                                                child_code_parts.append(f"RxNorm: {child_rxnorm}")
                                            if child_icd10:
                                                child_code_parts.append(f"ICD-10: {child_icd10}")
                                            if child_code_parts:
                                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;💊 {' • '.join(child_code_parts)}")
                                            
                                            # Child metadata
                                            meta_parts = []
                                            if child_page:
                                                meta_parts.append(f"📄 Page {child_page}")
                                            if child_date:
                                                meta_parts.append(f"📅 {child_date}")
                                            if child_evidence_boxes:
                                                meta_parts.append(f"📊 {len(child_evidence_boxes)} evidence boxes")
                                            if meta_parts:
                                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;{' • '.join(meta_parts)}")
                                            
                                            # Show child description
                                            if child_description:
                                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;_{child_description}_")
                                            
                                            # Show evidence text
                                            if child_evidence_text and child_evidence_text.lower() != child_name.lower():
                                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;💬 \\\"{child_evidence_text}\\\"")
                                            
                                            # Child page navigation button
                                            if child_page and child_evidence_boxes:
                                                evidence_dict = {"evidence": child_evidence_boxes}
                                                if st.button(f"📄 View on Page {child_page}", key=f"main_rxnorm_child_{group_count}_{child_idx}", type="secondary"):
                                                    navigate_to_page_with_condition(child_page, evidence_dict)
                                                    st.rerun()
                                            
                                            if child_idx < len(children) - 1:
                                                st.markdown("&nbsp;")
                                
                                group_count += 1
                            else:
                                # Children without parent - display them normally
                                for child in children:
                                    medication_name = child.get("title") or child.get("condition") or "Unknown"
                                    rxnorm_code = child.get("rxnorm_code", "")
                                    icd10_code = child.get("icd10_code", "")
                                    page_ref = child.get("page_reference") or child.get("page")
                                    date_reported = child.get("date") or child.get("date_reported", "")
                                    evidence_boxes = child.get("evidence_boxes", [])
                                    description = child.get("description", "")
                                    evidence_text = child.get("evidence") or child.get("original_condition", "")
                                    
                                    st.markdown(f"**{medication_name}**")
                                    if rxnorm_code or icd10_code:
                                        code_parts = []
                                        if rxnorm_code:
                                            code_parts.append(f"RxNorm: {rxnorm_code}")
                                        if icd10_code:
                                            code_parts.append(f"ICD-10: {icd10_code}")
                                        st.caption(f"💊 {' • '.join(code_parts)}")
                                    
                                    if description:
                                        st.markdown(f"_{description}_")
                                    
                                    if evidence_text and evidence_text.lower() != medication_name.lower():
                                        st.caption(f"💬 \\\"{evidence_text}\\\"")
                                    
                                    if page_ref and evidence_boxes:
                                        evidence_dict = {"evidence": evidence_boxes}
                                        if st.button(f"📄 Page {page_ref} • 📅 {date_reported}", key=f"main_rxnorm_child_noparent_{group_count}", type="secondary"):
                                            navigate_to_page_with_condition(page_ref, evidence_dict)
                                            st.rerun()
                                    
                                    st.divider()
                                    group_count += 1
                        
                        # Display standalone medications from hierarchical set
                        for idx, concern in enumerate(standalone):
                            medication_name = concern.get("title") or concern.get("condition") or "Unknown Medication"
                            rxnorm_code = concern.get("rxnorm_code")
                            icd10_code = concern.get("icd10_code")
                            page_ref = concern.get("page_reference") or concern.get("page")
                            date_reported = concern.get("date") or concern.get("date_reported", "")
                            evidence_boxes = concern.get("evidence_boxes", [])
                            description = concern.get("description", "")
                            evidence_text = concern.get("evidence") or concern.get("original_condition", "")
                            
                            st.markdown(f"**{medication_name}**")
                            if rxnorm_code or icd10_code:
                                code_parts = []
                                if rxnorm_code:
                                    code_parts.append(f"RxNorm: {rxnorm_code}")
                                if icd10_code:
                                    code_parts.append(f"ICD-10: {icd10_code}")
                                st.caption(f"💊 {' • '.join(code_parts)}")
                            
                            if description:
                                st.markdown(f"_{description}_")
                            
                            if evidence_text and evidence_text.lower() != medication_name.lower():
                                st.caption(f"💬 \\\"{evidence_text}\\\"")
                            
                            if page_ref and evidence_boxes:
                                evidence_dict = {"evidence": evidence_boxes}
                                if st.button(f"📄 Page {page_ref} • 📅 {date_reported}", key=f"main_rxnorm_standalone_{idx}", type="secondary"):
                                    navigate_to_page_with_condition(page_ref, evidence_dict)
                                    st.rerun()
                            
                            st.divider()
                    
                    # Display medications without hierarchical metadata (flat display)
                    if medications_without_hierarchy:
                        # Sort by date (most recent first)
                        rxnorm_sorted = sorted(
                            medications_without_hierarchy,
                            key=lambda x: x.get("date") or x.get("date_reported") or "1900-01-01",
                            reverse=True
                        )
                        
                        for idx, concern in enumerate(rxnorm_sorted):
                            medication_name = concern.get("title") or concern.get("condition") or "Unknown Medication"
                            description = concern.get("description") or ""
                            date_reported = concern.get("date") or concern.get("date_reported") or ""
                            page_ref = concern.get("page_reference") or concern.get("page")
                            category = concern.get("category", "")
                            evidence = concern.get("evidence") or concern.get("original_condition") or ""
                            rxnorm_code = concern.get("rxnorm_code")
                            icd10_code = concern.get("icd10_code")
                            
                            with st.container():
                                st.markdown(f"**{idx + 1}. {medication_name}**")
                                
                                # Show medical codes (RxNorm, ICD-10)
                                code_parts = []
                                if rxnorm_code:
                                    code_parts.append(f"RxNorm: {rxnorm_code}")
                                if icd10_code:
                                    code_parts.append(f"ICD-10: {icd10_code}")
                                if code_parts:
                                    st.caption(f"💊 {' • '.join(code_parts)}")
                                
                                # Show category and page link inline
                                category_parts = []
                                if category:
                                    category_parts.append(f"📂 {category}")
                                if page_ref:
                                    category_parts.append(f"📄 Page {page_ref}")
                                if category_parts:
                                    category_line = " • ".join(category_parts)
                                    # Priority 1: Use evidence_boxes directly from backend (already enriched)
                                    evidence_boxes = concern.get("evidence_boxes", [])
                                    
                                    if evidence_boxes:
                                        # Create evidence dict directly from bbox array
                                        evidence_dict = {"evidence": evidence_boxes}
                                        if st.button(category_line, key=f"main_rxnorm_med_flat_{idx}_eb", type="secondary", width='content'):
                                            navigate_to_page_with_condition(page_ref, evidence_dict)
                                            st.rerun()
                                    else:
                                        # Priority 2: Fallback to merge from both sources
                                        search_text = evidence or medication_name
                                        merged_bboxes = merge_bbox_from_both_sources(
                                            search_text,
                                            coded_conditions,
                                            medical_history_boxes + health_history_boxes
                                        )
                                        
                                        if merged_bboxes:
                                            evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                                            if st.button(category_line, key=f"main_rxnorm_med_flat_{idx}_mb", type="secondary", width='content'):
                                                navigate_to_page_with_condition(page_ref, evidence_dict)
                                                st.rerun()
                                        else:
                                            # Priority 3: Page-only navigation (no highlighting)
                                            if st.button(category_line, key=f"main_rxnorm_med_flat_{idx}_nb", type="secondary", width='content'):
                                                st.session_state.pdf_page = page_ref
                                                st.session_state.highlight_bbox = None
                                                st.rerun()
                                
                                # Show clinical summary
                                if description:
                                    st.markdown(f"_{description}_")
                                
                                # Show evidence if different from medication name
                                if evidence and evidence.lower() != medication_name.lower():
                                    st.caption(f"💬 Document text: \"{evidence}\"")
                                
                                # Show date
                                if date_reported:
                                    st.caption(f"📅 {date_reported}")
                                
                                st.divider()
                
                # Display Hierarchical SNOMED Conditions with Parent-Child Grouping
                if hierarchical_snomed:
                    st.markdown("")
                    st.markdown("#### 🔬 Hierarchical High-Risk Conditions")
                    st.caption(f"{len(hierarchical_snomed)} condition{'s' if len(hierarchical_snomed) > 1 else ''} identified through hierarchical clinical coding")
                    
                    # Group the conditions
                    hierarchical_groups, standalone = group_hierarchical_for_summary(hierarchical_snomed)
                    
                    # Display hierarchical groups with collapsible parent-child
                    group_count = 0
                    for parent_code, group in hierarchical_groups.items():
                        parent = group["parent"]
                        children = group["children"]
                        
                        if parent:
                            # Display parent with collapsible children
                            parent_name = parent.get("title") or parent.get("condition") or "Unknown Condition"
                            parent_path = parent.get("path_display", parent_name)
                            parent_snomed = parent.get("snomed_code", "")
                            parent_icd10 = parent.get("icd10_code", "")
                            parent_category = parent.get("category", "")
                            parent_page = parent.get("page_reference") or parent.get("page")
                            parent_date = parent.get("date_reported", "")
                            parent_evidence_boxes = parent.get("evidence_boxes", [])
                            
                            # Parent section with expander for children
                            with st.expander(f"**{parent_name}** ({len(children)} related finding{'s' if len(children) != 1 else ''})", expanded=True):
                                # Parent details
                                st.markdown(f"**Parent Condition:** {parent_name}")
                                
                                # Show codes
                                code_parts = []
                                if parent_snomed:
                                    code_parts.append(f"SNOMED: {parent_snomed}")
                                if parent_icd10:
                                    code_parts.append(f"ICD-10: {parent_icd10}")
                                if code_parts:
                                    st.caption(f"🏥 {' • '.join(code_parts)}")
                                
                                # Show category and page
                                if parent_category or parent_page:
                                    category_parts = []
                                    if parent_category:
                                        category_parts.append(f"📂 {parent_category}")
                                    if parent_page:
                                        category_parts.append(f"📄 Page {parent_page}")
                                    if parent_date:
                                        category_parts.append(f"📅 {parent_date}")
                                    st.caption(" • ".join(category_parts))
                                
                                # Parent page navigation button
                                if parent_page and parent_evidence_boxes:
                                    evidence_dict = {"evidence": parent_evidence_boxes}
                                    if st.button(f"📄 View Parent on Page {parent_page}", key=f"hier_parent_{group_count}", type="secondary"):
                                        navigate_to_page_with_condition(parent_page, evidence_dict)
                                        st.rerun()
                                
                                st.markdown("---")
                                
                                # Display children
                                if children:
                                    st.markdown(f"**└─ Specific Findings ({len(children)}):**")
                                    for child_idx, child in enumerate(children):
                                        child_name = child.get("title") or child.get("condition") or "Unknown"
                                        child_snomed = child.get("snomed_code", "")
                                        child_icd10 = child.get("icd10_code", "")
                                        child_page = child.get("page_reference") or child.get("page")
                                        child_date = child.get("date_reported", "")
                                        child_evidence_boxes = child.get("evidence_boxes", [])
                                        child_evidence_text = child.get("evidence") or child.get("original_condition", "")
                                        
                                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;**{child_idx + 1}. {child_name}**")
                                        
                                        # Child codes
                                        child_code_parts = []
                                        if child_snomed:
                                            child_code_parts.append(f"SNOMED: {child_snomed}")
                                        if child_icd10:
                                            child_code_parts.append(f"ICD-10: {child_icd10}")
                                        if child_code_parts:
                                            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;🏥 {' • '.join(child_code_parts)}")
                                        
                                        # Child metadata
                                        meta_parts = []
                                        if child_page:
                                            meta_parts.append(f"📄 Page {child_page}")
                                        if child_date:
                                            meta_parts.append(f"📅 {child_date}")
                                        if child_evidence_boxes:
                                            meta_parts.append(f"📊 {len(child_evidence_boxes)} evidence boxes")
                                        if meta_parts:
                                            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;{' • '.join(meta_parts)}")
                                        
                                        # Show evidence text
                                        if child_evidence_text and child_evidence_text.lower() != child_name.lower():
                                            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;💬 \"{child_evidence_text}\"")
                                        
                                        # Child page navigation button
                                        if child_page and child_evidence_boxes:
                                            evidence_dict = {"evidence": child_evidence_boxes}
                                            if st.button(f"📄 View on Page {child_page}", key=f"hier_child_{group_count}_{child_idx}", type="secondary"):
                                                navigate_to_page_with_condition(child_page, evidence_dict)
                                                st.rerun()
                                        
                                        if child_idx < len(children) - 1:
                                            st.markdown("&nbsp;")
                            
                            group_count += 1
                        else:
                            # Children without parent - display them normally
                            for child in children:
                                condition_name_raw = child.get("title") or child.get("condition") or "Unknown"
                                condition_name = to_camel_title(condition_name_raw)
                                snomed_code = child.get("snomed_code", "")
                                icd10_code = child.get("icd10_code", "")
                                page_ref = child.get("page_reference") or child.get("page")
                                date_reported = child.get("date_reported", "")
                                evidence_boxes = child.get("evidence_boxes", [])
                                
                                st.markdown(f"**{condition_name}**")
                                if snomed_code or icd10_code:
                                    code_parts = []
                                    if snomed_code:
                                        code_parts.append(f"SNOMED: {snomed_code}")
                                    if icd10_code:
                                        code_parts.append(f"ICD-10: {icd10_code}")
                                    st.caption(f"🏥 {' • '.join(code_parts)}")
                                
                                if page_ref and evidence_boxes:
                                    evidence_dict = {"evidence": evidence_boxes}
                                    if st.button(f"📄 Page {page_ref} • 📅 {date_reported}", key=f"hier_child_noparent_{group_count}", type="secondary"):
                                        navigate_to_page_with_condition(page_ref, evidence_dict)
                                        st.rerun()
                                
                                st.divider()
                                group_count += 1
                    
                    # Display standalone conditions (no hierarchy)
                    for idx, concern in enumerate(standalone):
                        condition_name_raw = concern.get("title") or concern.get("condition") or "Unknown Condition"
                        condition_name = to_camel_title(condition_name_raw)
                        snomed_code = concern.get("snomed_code")
                        icd10_code = concern.get("icd10_code")
                        page_ref = concern.get("page_reference") or concern.get("page")
                        date_reported = concern.get("date_reported", "")
                        evidence_boxes = concern.get("evidence_boxes", [])
                        
                        st.markdown(f"**{condition_name}**")
                        if snomed_code or icd10_code:
                            code_parts = []
                            if snomed_code:
                                code_parts.append(f"SNOMED: {snomed_code}")
                            if icd10_code:
                                code_parts.append(f"ICD-10: {icd10_code}")
                            st.caption(f"🏥 {' • '.join(code_parts)}")
                        
                        if page_ref and evidence_boxes:
                            evidence_dict = {"evidence": evidence_boxes}
                            if st.button(f"📄 Page {page_ref} • 📅 {date_reported}", key=f"hier_standalone_{idx}", type="secondary"):
                                navigate_to_page_with_condition(page_ref, evidence_dict)
                                st.rerun()
                        
                        st.divider()
                
                # Display Hierarchical RxNorm Medications with Parent-Child Grouping
                if hierarchical_rxnorm:
                    st.markdown("")
                    st.markdown("#### 🧬 Hierarchical High-Risk Medications")
                    st.caption(f"{len(hierarchical_rxnorm)} medication{'s' if len(hierarchical_rxnorm) > 1 else ''} identified through hierarchical drug coding")
                    
                    # Group medications by parent-child relationships (reuse function from SNOMED)
                    hierarchical_rx_groups, standalone_rx = group_hierarchical_for_summary(hierarchical_rxnorm)
                    
                    # Display hierarchical groups with collapsible parent-child
                    rx_group_count = 0
                    for parent_code, group in hierarchical_rx_groups.items():
                        parent = group["parent"]
                        children = group["children"]
                        
                        if parent:
                            # Display parent with collapsible children
                            parent_name = parent.get("title") or parent.get("condition") or "Unknown Medication"
                            parent_path = parent.get("path_display", parent_name)
                            parent_rxnorm = parent.get("rxnorm_code") or parent.get("snomed_code", "")
                            parent_category = parent.get("category", "")
                            parent_page = parent.get("page_reference") or parent.get("page")
                            parent_date = parent.get("date_reported", "")
                            parent_evidence_boxes = parent.get("evidence_boxes", [])
                            
                            # Parent section with expander for children
                            with st.expander(f"**{parent_name}** ({len(children)} specific formulation{'s' if len(children) != 1 else ''})", expanded=True):
                                # Parent details
                                st.markdown(f"**Parent Medication:** {parent_name}")
                                
                                # Show codes
                                if parent_rxnorm:
                                    st.caption(f"💊 RxNorm: {parent_rxnorm}")
                                
                                # Show category and page
                                if parent_category or parent_page:
                                    category_parts = []
                                    if parent_category:
                                        category_parts.append(f"📂 {parent_category}")
                                    if parent_page:
                                        category_parts.append(f"📄 Page {parent_page}")
                                    if parent_date:
                                        category_parts.append(f"📅 {parent_date}")
                                    st.caption(" • ".join(category_parts))
                                
                                # Parent page navigation button
                                if parent_page and parent_evidence_boxes:
                                    evidence_dict = {"evidence": parent_evidence_boxes}
                                    if st.button(f"📄 View Parent on Page {parent_page}", key=f"hier_rx_parent_{rx_group_count}", type="secondary"):
                                        navigate_to_page_with_condition(parent_page, evidence_dict)
                                        st.rerun()
                                
                                st.markdown("---")
                                
                                # Display children
                                if children:
                                    st.markdown(f"**└─ Specific Formulations ({len(children)}):**")
                                    for child_idx, child in enumerate(children):
                                        child_name = child.get("title") or child.get("condition") or "Unknown"
                                        child_rxnorm = child.get("rxnorm_code") or child.get("snomed_code", "")
                                        child_page = child.get("page_reference") or child.get("page")
                                        child_date = child.get("date_reported", "")
                                        child_evidence_boxes = child.get("evidence_boxes", [])
                                        child_evidence_text = child.get("evidence") or child.get("original_condition", "")
                                        
                                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;**{child_idx + 1}. {child_name}**")
                                        
                                        # Child codes
                                        if child_rxnorm:
                                            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;💊 RxNorm: {child_rxnorm}")
                                        
                                        # Child metadata
                                        meta_parts = []
                                        if child_page:
                                            meta_parts.append(f"📄 Page {child_page}")
                                        if child_date:
                                            meta_parts.append(f"📅 {child_date}")
                                        if child_evidence_boxes:
                                            meta_parts.append(f"📊 {len(child_evidence_boxes)} evidence boxes")
                                        if meta_parts:
                                            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;{' • '.join(meta_parts)}")
                                        
                                        # Show evidence text
                                        if child_evidence_text and child_evidence_text.lower() != child_name.lower():
                                            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;💬 \"{child_evidence_text}\"")
                                        
                                        # Child page navigation button
                                        if child_page and child_evidence_boxes:
                                            evidence_dict = {"evidence": child_evidence_boxes}
                                            if st.button(f"📄 View on Page {child_page}", key=f"hier_rx_child_{rx_group_count}_{child_idx}", type="secondary"):
                                                navigate_to_page_with_condition(child_page, evidence_dict)
                                                st.rerun()
                                        
                                        if child_idx < len(children) - 1:
                                            st.markdown("&nbsp;")
                            
                            rx_group_count += 1
                        else:
                            # Children without parent - display them normally
                            for child in children:
                                medication_name = child.get("title") or child.get("condition") or "Unknown"
                                rxnorm_code = child.get("rxnorm_code") or child.get("snomed_code", "")
                                page_ref = child.get("page_reference") or child.get("page")
                                date_reported = child.get("date_reported", "")
                                evidence_boxes = child.get("evidence_boxes", [])
                                
                                st.markdown(f"**{medication_name}**")
                                if rxnorm_code:
                                    st.caption(f"💊 RxNorm: {rxnorm_code}")
                                
                                if page_ref and evidence_boxes:
                                    evidence_dict = {"evidence": evidence_boxes}
                                    if st.button(f"📄 Page {page_ref} • 📅 {date_reported}", key=f"hier_rx_child_noparent_{rx_group_count}", type="secondary"):
                                        navigate_to_page_with_condition(page_ref, evidence_dict)
                                        st.rerun()
                                
                                st.divider()
                                rx_group_count += 1
                    
                    # Display standalone medications (no hierarchy)
                    for idx, concern in enumerate(standalone_rx):
                        medication_name = concern.get("title") or concern.get("condition") or "Unknown Medication"
                        rxnorm_code = concern.get("rxnorm_code") or concern.get("snomed_code")
                        page_ref = concern.get("page_reference") or concern.get("page")
                        date_reported = concern.get("date_reported", "")
                        evidence_boxes = concern.get("evidence_boxes", [])
                        
                        st.markdown(f"**{medication_name}**")
                        if rxnorm_code:
                            st.caption(f"💊 RxNorm: {rxnorm_code}")
                        
                        if page_ref and evidence_boxes:
                            evidence_dict = {"evidence": evidence_boxes}
                            if st.button(f"📄 Page {page_ref} • 📅 {date_reported}", key=f"hier_rx_standalone_{idx}", type="secondary"):
                                navigate_to_page_with_condition(page_ref, evidence_dict)
                                st.rerun()
                        
                        st.divider()
                
                if not main_snomed_conditions and not main_rxnorm_medications and not hierarchical_snomed and not hierarchical_rxnorm:
                    st.success("✅ No high-risk conditions or medications identified.")
            else:
                st.success("✅ No high-risk conditions or medications identified.")
        
        # =========================================================
        # SECTION: HEALTH OVERVIEW
        # =========================================================
        elif section == "Health Overview":
            
            st.subheader("Health Overview")
            
            health_overview = data.get("health_overview", {})
            
            # Get vital values (use height_display for user-friendly format, height_cm is numeric for calculations)
            height_val = health_overview.get('height_display', 'N/A')
            weight_val = health_overview.get('weight_kg', 'N/A')
            bmi_val = health_overview.get('bmi', 'N/A')
            bp_val = health_overview.get('bp', 'N/A')
            hr_val = health_overview.get('heart_rate', 'N/A')
            
            # Extract patient age for age-specific vital thresholds and chart zones
            vitals_thresholds = health_overview.get("vitals_thresholds_applied", {})
            patient_age = vitals_thresholds.get("age", 30) if vitals_thresholds else 30
            
            # Get status for each vital (pass age for dynamic thresholds)
            bmi_status = get_vital_status("bmi", bmi_val, age=patient_age)
            
            # Parse BP into systolic and diastolic
            bp_systolic_val = None
            bp_diastolic_val = None
            if bp_val != 'N/A' and isinstance(bp_val, str) and '/' in bp_val:
                try:
                    parts = bp_val.split('/')
                    bp_systolic_val = parts[0].strip()
                    bp_diastolic_val = parts[1].replace('mmHg', '').replace('mm Hg', '').strip()
                except:
                    pass
            
            # Use combined BP status (AHA 2017 guidelines - more accurate than individual)
            bp_combined_status = get_bp_combined_status(bp_systolic_val, bp_diastolic_val)
            hr_status = get_vital_status("heart_rate", hr_val, age=patient_age)
            
            c1, c2, c3, c4, c5 = st.columns(5)
            
            # Display metrics with color-coded status indicators
            c1.metric("Height", height_val)
            c2.metric("Weight", weight_val)
            
            # BMI with color indicator
            with c3:
                st.metric("BMI", bmi_val)
                if bmi_status["status"] != "unknown":
                    st.markdown(f"<span style='color: {bmi_status['color']}; font-size: 0.8rem;'>\u25cf {bmi_status['label']}</span>", unsafe_allow_html=True)
            
            # Blood Pressure with AHA 2017 combined classification
            with c4:
                st.metric("Blood Pressure", f"{bp_val} mmHg" if bp_val != 'N/A' else "N/A")
                if bp_combined_status["status"] != "unknown":
                    st.markdown(f"<span style='color: {bp_combined_status['color']}; font-size: 0.8rem;'>\u25cf {bp_combined_status['label']}</span>", unsafe_allow_html=True)
            
            # Heart Rate with color indicator
            with c5:
                st.metric("Heart Rate", f"{hr_val} bpm" if hr_val != 'N/A' else "N/A")
                if hr_status["status"] != "unknown":
                    st.markdown(f"<span style='color: {hr_status['color']}; font-size: 0.8rem;'>\u25cf {hr_status['label']}</span>", unsafe_allow_html=True)
            
            st.markdown("---")
            
            # Vitals Trend
            st.subheader("Vitals Trend")
            
            vitals_trend = health_overview.get("vitals_trend", {})
            
            # Debug: Log vitals trend data for verification
            logger.info(f"Vitals Trend Data - Dates: {vitals_trend.get('dates', [])}")
            logger.info(f"Vitals Trend Data - Weights: {vitals_trend.get('weights', [])}")
            logger.info(f"Vitals Trend Data - BP Systolic: {vitals_trend.get('bp_systolic', [])}")
            logger.info(f"Vitals Trend Data - BP Diastolic: {vitals_trend.get('bp_diastolic', [])}")
            logger.info(f"Vitals Trend Data - Heart Rate: {vitals_trend.get('heart_rate', [])}")
            
            if vitals_trend and vitals_trend.get("dates"):
                
                # Filter out invalid dates before parsing and track valid indices
                raw_dates = vitals_trend.get("dates", [])
                valid_indices = []
                valid_dates = []
                
                for idx, d in enumerate(raw_dates):
                    if d and d not in ["Not specified", "N/A", "Unknown", ""]:
                        valid_indices.append(idx)
                        valid_dates.append(d)
                
                if not valid_dates:
                    st.info("No valid dates available for vitals trend visualization")
                    dates = pd.to_datetime([])
                else:
                    try:
                        dates = pd.to_datetime(valid_dates, errors='coerce')
                    except Exception as e:
                        logger.error(f"Error parsing vitals dates: {e}")
                        st.warning(f"Could not parse vitals dates: {e}")
                        dates = pd.to_datetime([])
                
                # Helper function to parse and validate numeric values
                def parse_numeric_array(values, value_type="value"):
                    """Parse array of values, converting to float or None. Returns list of same length."""
                    if not values:
                        return []
                    
                    parsed = []
                    for v in values:
                        if v is None or (isinstance(v, str) and v.lower() in ["not specified", "n/a", "na", "unknown", ""]):
                            parsed.append(None)
                        elif isinstance(v, (int, float)):
                            parsed.append(float(v))
                        elif isinstance(v, str):
                            # Try to extract numeric value from string
                            try:
                                # Remove common units and parse
                                cleaned = v.strip().replace("lb", "").replace("kg", "").replace("bpm", "").replace("mmHg", "").strip()
                                parsed.append(float(cleaned))
                            except ValueError:
                                # Could not parse as number
                                parsed.append(None)
                        else:
                            parsed.append(None)
                    return parsed
                
                def parse_bp_array(bp_values):
                    """Parse BP values which may be numeric pairs or strings. Returns (systolic_list, diastolic_list)"""
                    if not bp_values:
                        return [], []
                    
                    systolic = []
                    diastolic = []
                    
                    for bp in bp_values:
                        if bp is None or (isinstance(bp, str) and bp.lower() in ["not specified", "n/a", "na", "unknown", ""]):
                            systolic.append(None)
                            diastolic.append(None)
                        elif isinstance(bp, str) and "/" in bp:
                            # Parse "120/80" format
                            try:
                                parts = bp.split("/")
                                sys_val = float(parts[0].strip())
                                dia_val = float(parts[1].strip())
                                systolic.append(sys_val)
                                diastolic.append(dia_val)
                            except (ValueError, IndexError):
                                systolic.append(None)
                                diastolic.append(None)
                        elif isinstance(bp, dict):
                            # Handle structured BP: {"systolic": 120, "diastolic": 80}
                            systolic.append(bp.get("systolic"))
                            diastolic.append(bp.get("diastolic"))
                        else:
                            # String without numeric format (e.g., "Elevated", "Controlled on medication")
                            systolic.append(None)
                            diastolic.append(None)
                    
                    return systolic, diastolic
                
                # Support both "weight" and "weights" keys
                weights_raw = vitals_trend.get("weights") or vitals_trend.get("weight", [])
                heart_rate_raw = vitals_trend.get("heart_rate", [])
                bp_raw = vitals_trend.get("bp", [])
                
                # Filter raw arrays to match valid date indices
                def filter_by_valid_indices(arr, valid_indices):
                    """Keep only elements at valid_indices positions"""
                    if not arr:
                        return []
                    filtered = []
                    for idx in valid_indices:
                        if idx < len(arr):
                            filtered.append(arr[idx])
                        else:
                            filtered.append(None)
                    return filtered
                
                weights_raw = filter_by_valid_indices(weights_raw, valid_indices)
                heart_rate_raw = filter_by_valid_indices(heart_rate_raw, valid_indices)
                bp_raw = filter_by_valid_indices(bp_raw, valid_indices)
                
                # Parse values to numeric or None
                weights = parse_numeric_array(weights_raw, "weight")
                heart_rate = parse_numeric_array(heart_rate_raw, "heart_rate")
                
                # Handle BP - check if already split into systolic/diastolic or needs parsing
                if vitals_trend.get("bp_systolic") and vitals_trend.get("bp_diastolic"):
                    bp_systolic_raw = filter_by_valid_indices(vitals_trend.get("bp_systolic", []), valid_indices)
                    bp_diastolic_raw = filter_by_valid_indices(vitals_trend.get("bp_diastolic", []), valid_indices)
                    bp_systolic = parse_numeric_array(bp_systolic_raw, "bp_systolic")
                    bp_diastolic = parse_numeric_array(bp_diastolic_raw, "bp_diastolic")
                else:
                    bp_systolic, bp_diastolic = parse_bp_array(bp_raw)
                
                # Ensure all arrays are the same length as dates
                num_dates = len(dates)
                if len(weights) != num_dates:
                    logger.warning(f"Mismatch: {num_dates} dates but {len(weights)} weights. Padding with None.")
                    weights = (weights + [None] * num_dates)[:num_dates]
                if len(bp_systolic) != num_dates:
                    logger.warning(f"Mismatch: {num_dates} dates but {len(bp_systolic)} bp_systolic values. Padding with None.")
                    bp_systolic = (bp_systolic + [None] * num_dates)[:num_dates]
                    bp_diastolic = (bp_diastolic + [None] * num_dates)[:num_dates]
                if len(heart_rate) != num_dates:
                    logger.warning(f"Mismatch: {num_dates} dates but {len(heart_rate)} heart_rate values. Padding with None.")
                    heart_rate = (heart_rate + [None] * num_dates)[:num_dates]
                
                # Helper function to get age-specific chart zone boundaries
                def get_chart_zones_for_age(patient_age):
                    """
                    Returns age-specific Systolic BP, Diastolic BP, HR, and BMI zone boundaries for chart visualization.
                    Updated to align with AHA 2017 guidelines and current medical standards.
                    
                    Note: Pediatric BMI uses fixed values as approximation. Clinical practice requires
                    age-and-sex-specific CDC percentile charts for accurate BMI classification in children.
                    """
                    # Default to adult if age parsing fails
                    try:
                        age_num = float(patient_age) if patient_age and str(patient_age).strip().lower() != "n/a" else 30
                    except (ValueError, TypeError):
                        age_num = 30
                    
                    # Determine age group and corresponding zones
                    if age_num < 1/12:  # Newborn (0-1 month)
                        sys_bp_zones = {"severe_low": (40, 60), "mild_low": (60, 70), "normal": (70, 95), "mild_high": (95, 105), "severe_high": (105, 120)}
                        dia_bp_zones = {"severe_low": (20, 30), "mild_low": (30, 45), "normal": (45, 65), "mild_high": (65, 72), "severe_high": (72, 85)}
                        hr_zones = {"severe_low": (30, 95), "mild_low": (95, 100), "normal": (100, 180), "mild_high": (180, 190), "severe_high": (190, 220)}
                        bmi_zones = None  # BMI not used for newborns (use weight-for-length charts)
                        sys_y_range_bp = [40, 120]
                        dia_y_range_bp = [20, 85]
                        y_range_hr = [30, 220]
                    elif age_num < 1:  # Infant (1-12 months)
                        sys_bp_zones = {"severe_low": (40, 70), "mild_low": (70, 78), "normal": (78, 108), "mild_high": (108, 118), "severe_high": (118, 140)}
                        dia_bp_zones = {"severe_low": (25, 40), "mild_low": (40, 52), "normal": (52, 72), "mild_high": (72, 78), "severe_high": (78, 90)}
                        hr_zones = {"severe_low": (30, 85), "mild_low": (85, 90), "normal": (90, 160), "mild_high": (160, 170), "severe_high": (170, 200)}
                        bmi_zones = None  # BMI not used for infants (use weight-for-length charts)
                        sys_y_range_bp = [40, 140]
                        dia_y_range_bp = [25, 90]
                        y_range_hr = [30, 200]
                    elif age_num < 13:  # Child (1-12 years)
                        # Note: BP varies significantly by age/height. Using general estimates.
                        sys_bp_zones = {"severe_low": (40, 75), "mild_low": (75, 88), "normal": (88, 118), "mild_high": (118, 128), "severe_high": (128, 150)}
                        dia_bp_zones = {"severe_low": (30, 45), "mild_low": (45, 58), "normal": (58, 78), "mild_high": (78, 85), "severe_high": (85, 100)}
                        hr_zones = {"severe_low": (30, 65), "mild_low": (65, 70), "normal": (70, 120), "mild_high": (120, 135), "severe_high": (135, 170)}
                        # Note: BMI for children should use CDC percentiles. Fixed values are approximations only.
                        bmi_zones = {"severe_low": 13, "underweight": 14.5, "normal_low": 14.5, "normal_high": 21, "overweight": 24, "obese": 27}
                        sys_y_range_bp = [40, 150]
                        dia_y_range_bp = [30, 100]
                        y_range_hr = [30, 170]
                    elif age_num < 18:  # Adolescent (13-17 years)
                        # Transitioning toward adult ranges
                        sys_bp_zones = {"severe_low": (40, 85), "mild_low": (85, 98), "normal": (98, 118), "mild_high": (118, 135), "severe_high": (135, 170)}
                        dia_bp_zones = {"severe_low": (40, 55), "mild_low": (55, 63), "normal": (63, 78), "mild_high": (78, 88), "severe_high": (88, 110)}
                        hr_zones = {"severe_low": (30, 55), "mild_low": (55, 60), "normal": (60, 100), "mild_high": (100, 115), "severe_high": (115, 150)}
                        # Note: BMI for adolescents should use CDC percentiles. Fixed values are approximations only.
                        bmi_zones = {"severe_low": 16, "underweight": 17.5, "normal_low": 17.5, "normal_high": 24, "overweight": 28, "obese": 31}
                        sys_y_range_bp = [40, 170]
                        dia_y_range_bp = [40, 110]
                        y_range_hr = [30, 150]
                    elif age_num <= 65:  # Adult (18-65 years) - AHA 2017 Guidelines
                        # BP Categories: Normal <120/80, Elevated 120-129/<80, Stage 1 HTN 130-139/80-89, Stage 2 ≥140/90
                        sys_bp_zones = {"severe_low": (40, 85), "mild_low": (85, 90), "normal": (90, 120), "mild_high": (120, 140), "severe_high": (140, 200)}
                        dia_bp_zones = {"severe_low": (40, 55), "mild_low": (55, 60), "normal": (60, 80), "mild_high": (80, 90), "severe_high": (90, 130)}
                        hr_zones = {"severe_low": (30, 55), "mild_low": (55, 60), "normal": (60, 100), "mild_high": (100, 110), "severe_high": (110, 180)}
                        # WHO/CDC Standard BMI: Underweight <18.5, Normal 18.5-24.9, Overweight 25-29.9, Obese ≥30
                        bmi_zones = {"severe_low": 15, "underweight": 18.5, "normal_low": 18.5, "normal_high": 25.0, "overweight": 30.0, "obese": 35}
                        sys_y_range_bp = [40, 200]
                        dia_y_range_bp = [40, 130]
                        y_range_hr = [30, 180]
                    else:  # Elderly (>65 years) - Relaxed targets per geriatric guidelines
                        # BP targets slightly higher acceptable (up to 130-140 systolic per some guidelines)
                        sys_bp_zones = {"severe_low": (40, 85), "mild_low": (85, 90), "normal": (90, 140), "mild_high": (140, 155), "severe_high": (155, 200)}
                        dia_bp_zones = {"severe_low": (40, 55), "mild_low": (55, 60), "normal": (60, 80), "mild_high": (80, 92), "severe_high": (92, 130)}
                        hr_zones = {"severe_low": (30, 50), "mild_low": (50, 60), "normal": (60, 100), "mild_high": (100, 115), "severe_high": (115, 180)}
                        # Geriatric evidence suggests slightly higher BMI (22-27) may be protective in older adults
                        bmi_zones = {"severe_low": 18, "underweight": 22, "normal_low": 22, "normal_high": 27, "overweight": 30, "obese": 35}
                        sys_y_range_bp = [40, 200]
                        dia_y_range_bp = [40, 130]
                        y_range_hr = [30, 180]
                    
                    return sys_bp_zones, dia_bp_zones, hr_zones, bmi_zones, sys_y_range_bp, dia_y_range_bp, y_range_hr
                
                # Get age-specific chart zones using patient_age extracted earlier
                sys_bp_zones, dia_bp_zones, hr_zones, bmi_zones, sys_y_range_bp, dia_y_range_bp, y_range_hr = get_chart_zones_for_age(patient_age)
                
                if len(dates) > 0:
                    # Create DataFrame with all vitals data
                    df_data = {"Date": dates}
                    
                    # Add weight if available (check for at least one non-None numeric value)
                    if weights and any(w is not None and isinstance(w, (int, float)) for w in weights):
                        df_data["Weight (lb)"] = weights
                    
                    # Add BP if available (check for at least one non-None numeric value)
                    if bp_systolic and any(bp is not None and isinstance(bp, (int, float)) for bp in bp_systolic):
                        df_data["Systolic BP"] = bp_systolic
                    if bp_diastolic and any(bp is not None and isinstance(bp, (int, float)) for bp in bp_diastolic):
                        df_data["Diastolic BP"] = bp_diastolic
                    
                    # Add heart rate if available (check for at least one non-None numeric value)
                    if heart_rate and any(hr is not None and isinstance(hr, (int, float)) for hr in heart_rate):
                        df_data["Heart Rate (bpm)"] = heart_rate
                    
                    df = pd.DataFrame(df_data)
                    
                    # Sort DataFrame by Date to ensure chronological order
                    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date  # Python date objects
                    df = df.sort_values("Date").reset_index(drop=True)
                    
                    logger.info(f"Created vitals DataFrame with {len(df)} rows, sorted by date")
                    logger.info(f"DataFrame columns: {df.columns.tolist()}")
                    logger.info(f"Date range: {df['Date'].min()} to {df['Date'].max()}")
                    
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.write("**Weight Trend**")
                        if "Weight (lb)" in df.columns:
                            # Filter out None values for trend line calculation
                            df_weight_valid = df[["Date", "Weight (lb)"]].dropna()
                            
                            # Check if we have any numeric data
                            if len(df_weight_valid) == 0:
                                st.info("No numeric weight data available")
                            else:
                                # Create interactive Plotly chart for weight
                                fig_weight = go.Figure()
                                
                                # Add reference zones based on patient's height and age-specific BMI ranges
                                try:
                                    # Get height from health overview
                                    health_overview = data.get("health_overview", {})
                                    height_cm = health_overview.get('height_cm')
                                    
                                    # Only add BMI zones if height is available AND BMI zones are defined for this age group
                                    if height_cm and isinstance(height_cm, (int, float)) and height_cm > 0 and bmi_zones is not None:
                                        height_m = height_cm / 100.0  # Convert cm to meters
                                        
                                        # Calculate weight thresholds based on age-specific BMI ranges (5-ZONE SYSTEM)
                                        # BMI = weight (kg) / height (m)^2
                                        # Weight (lb) = BMI * height^2 * 2.20462
                                        
                                        weight_severely_underweight = bmi_zones["severe_low"] * (height_m ** 2) * 2.20462
                                        weight_underweight = bmi_zones["underweight"] * (height_m ** 2) * 2.20462
                                        weight_normal_low = bmi_zones["normal_low"] * (height_m ** 2) * 2.20462
                                        weight_normal_max = bmi_zones["normal_high"] * (height_m ** 2) * 2.20462
                                        weight_overweight = bmi_zones["overweight"] * (height_m ** 2) * 2.20462
                                        weight_obese_max = bmi_zones["obese"] * (height_m ** 2) * 2.20462
                                        
                                        # Add reference zones (from bottom to top) - 5-ZONE SYMMETRICAL
                                        # Zone 1: Severely Underweight (RED)
                                        fig_weight.add_hrect(
                                            y0=80, y1=weight_severely_underweight,
                                            fillcolor="rgba(220, 53, 69, 0.2)",
                                            layer="below",
                                            line_width=0
                                        )
                                        
                                        # Zone 2: Underweight (ORANGE - matches legend)
                                        fig_weight.add_hrect(
                                            y0=weight_severely_underweight, y1=weight_normal_low,
                                            fillcolor="rgba(237, 139, 0, 0.2)",  # #ED8B00 - MODERATE_COLOR
                                            layer="below",
                                            line_width=0
                                        )
                                        
                                        # Zone 3: Normal Weight (GREEN) - Age-specific range
                                        fig_weight.add_hrect(
                                            y0=weight_normal_low, y1=weight_normal_max,
                                            fillcolor="rgba(134, 188, 37, 0.25)",
                                            layer="below",
                                            line_width=0
                                        )
                                        
                                        # Zone 4: Overweight (ORANGE - matches legend)
                                        fig_weight.add_hrect(
                                            y0=weight_normal_max, y1=weight_overweight,
                                            fillcolor="rgba(237, 139, 0, 0.2)",  # #ED8B00 - MODERATE_COLOR
                                            layer="below",
                                            line_width=0
                                        )
                                        
                                        # Zone 5: Obese (RED)
                                        fig_weight.add_hrect(
                                            y0=weight_overweight, y1=weight_obese_max,
                                            fillcolor="rgba(220, 53, 69, 0.2)",
                                            layer="below",
                                            line_width=0
                                        )
                                except Exception as e:
                                    logger.warning(f"Could not calculate weight reference zones: {e}")
                                
                                # Add data points and line
                                fig_weight.add_trace(go.Scatter(
                                    x=df_weight_valid["Date"],
                                    y=df_weight_valid["Weight (lb)"],
                                    mode='lines+markers',
                                    name='Weight',
                                    line=dict(color='#1D4F91', width=3),
                                    marker=dict(size=8, color='#1D4F91'),
                                    hovertemplate='<b>Date:</b> %{x|%Y-%m-%d}<br><b>Weight:</b> %{y:.1f} lb<extra></extra>'
                                ))
                                
                                # Add trend line if we have at least 2 points with valid numeric values
                                if len(df_weight_valid) >= 2:
                                    try:
                                        import numpy as np
                                        # Convert dates to numeric for trend calculation
                                        x_numeric = np.arange(len(df_weight_valid))
                                        y_values = df_weight_valid["Weight (lb)"].values
                                        
                                        # Ensure all y_values are numeric
                                        y_values = np.array([float(y) for y in y_values])
                                        
                                        # Calculate polynomial trend (degree 2 for curve, or 1 for linear if only 2 points)
                                        degree = min(2, len(df_weight_valid) - 1)
                                        z = np.polyfit(x_numeric, y_values, degree)
                                        p = np.poly1d(z)
                                        trend_y = p(x_numeric)
                                    except (ValueError, TypeError) as e:
                                        logger.warning(f"Could not calculate weight trend: {e}")
                                        trend_y = None
                                else:
                                    trend_y = None
                                
                                if trend_y is not None:
                                    fig_weight.add_trace(go.Scatter(
                                        x=df_weight_valid["Date"],
                                        y=trend_y,
                                        mode='lines',
                                        name='Trend',
                                        line=dict(color='#1D4F91', width=2, dash='dash'),
                                        hovertemplate='<b>Trend:</b> %{y:.1f} lb<extra></extra>'
                                    ))
                                
                                # Calculate y-axis range to show all zones
                                try:
                                    # Get min/max from data
                                    min_weight = df_weight_valid["Weight (lb)"].min()
                                    max_weight = df_weight_valid["Weight (lb)"].max()
                                    
                                    # Get zone boundaries if they were calculated (age-specific)
                                    if height_cm and isinstance(height_cm, (int, float)) and height_cm > 0 and bmi_zones is not None:
                                        height_m = height_cm / 100.0
                                        zone_min = 80  # Bottom of underweight zone
                                        zone_max = bmi_zones["obese"] * (height_m ** 2) * 2.20462  # Top of obese zone (age-specific)
                                        
                                        # Set range to include both data and all zones
                                        y_min = min(zone_min, min_weight - 10)
                                        y_max = max(zone_max, max_weight + 10)
                                    else:
                                        # Fallback: just pad the data range
                                        y_min = min_weight - 20
                                        y_max = max_weight + 20
                                except:
                                    # If calculation fails, use reasonable defaults
                                    y_min = 100
                                    y_max = 300
                                
                                fig_weight.update_layout(
                                    height=300,
                                    margin=dict(l=0, r=0, t=0, b=0),
                                    hovermode='x unified',
                                    plot_bgcolor='rgba(0,0,0,0)',
                                    paper_bgcolor='rgba(0,0,0,0)',
                                    legend=dict(
                                        orientation="h",
                                        yanchor="bottom",
                                        y=1.02,
                                        xanchor="right",
                                        x=1
                                    ),
                                    xaxis=dict(
                                        showgrid=True,
                                        gridwidth=1,
                                        gridcolor='rgba(128,128,128,0.3)'
                                    ),
                                    yaxis=dict(
                                        showgrid=True,
                                        gridwidth=1,
                                        gridcolor='rgba(128,128,128,0.3)',
                                        title='Weight (lb)',
                                        range=[y_min, y_max]
                                    )
                                )
                                st.plotly_chart(fig_weight, width='stretch')
                        else:
                            st.info("No weight data available")
                    
                    with col2:
                        st.write("**Blood Pressure Trend**")

                        if all(c in df.columns for c in ["Date", "Systolic BP", "Diastolic BP"]):
                            df_bp_valid = df[["Date", "Systolic BP", "Diastolic BP"]].copy()

                            # Robust Date parsing 
                            s = df_bp_valid["Date"]
                            if pd.api.types.is_numeric_dtype(s):
                                sn = pd.to_numeric(s, errors="coerce")
                                med = sn.dropna().median()

                                # If values look like YYYYMMDD (e.g., 20250115), parse as calendar date
                                if med is not None and 19000101 <= med <= 21001231:
                                    df_bp_valid["Date"] = pd.to_datetime(
                                        sn.astype("Int64").astype(str),
                                        format="%Y%m%d",
                                        errors="coerce",
                                    )
                                else:
                                    # Heuristic for epoch timestamps
                                    unit = "ms" if med and med > 1e12 else "s" if med and med > 1e9 else "D"
                                    df_bp_valid["Date"] = pd.to_datetime(sn, errors="coerce", unit=unit)
                            else:
                                df_bp_valid["Date"] = pd.to_datetime(s, errors="coerce")

                            df_bp_valid = df_bp_valid.dropna(subset=["Date", "Systolic BP", "Diastolic BP"]).sort_values("Date")

                            if len(df_bp_valid) == 0:
                                st.info("No numeric BP data available")
                            else:
                                fig_bp = make_subplots(
                                    rows=2,
                                    cols=1,
                                    shared_xaxes=True,
                                    vertical_spacing=0.10,
                                )

                                zone_colors = {
                                    "severe_low":  "rgba(220, 53, 69, 0.20)",   # red
                                    "mild_low":    "rgba(237, 139, 0, 0.20)",   # orange - matches legend #ED8B00
                                    "normal":      "rgba(134, 188, 37, 0.25)",  # green
                                    "mild_high":   "rgba(237, 139, 0, 0.20)",   # orange - matches legend #ED8B00
                                    "severe_high": "rgba(220, 53, 69, 0.20)",   # red
                                }

                                def add_zone_bands_shapes(fig, zones, *, yref: str, xref: str):
                                    for k in ["severe_low", "mild_low", "normal", "mild_high", "severe_high"]:
                                        lo, hi = zones[k]
                                        fig.add_shape(
                                            type="rect",
                                            xref=xref, yref=yref,
                                            x0=0, x1=1,                 # full width of subplot's x-domain
                                            y0=float(lo), y1=float(hi),
                                            fillcolor=zone_colors[k],
                                            opacity=1.0,
                                            layer="below",
                                            line=dict(width=0),
                                        )

                                add_zone_bands_shapes(fig_bp, sys_bp_zones, yref="y",  xref="x domain")   # top subplot
                                add_zone_bands_shapes(fig_bp, dia_bp_zones, yref="y2", xref="x2 domain")  # bottom subplot

                                # ---------
                                # Series
                                # ---------
                                fig_bp.add_trace(
                                    go.Scatter(
                                        x=df_bp_valid["Date"],
                                        y=df_bp_valid["Systolic BP"],
                                        mode="lines+markers",
                                        name="Systolic",
                                        line=dict(color="#007CB0", width=3),
                                        marker=dict(size=8, color="#007CB0"),
                                        hovertemplate="<b>Date:</b> %{x|%Y-%m-%d}<br><b>Systolic:</b> %{y} mmHg<extra></extra>",
                                    ),
                                    row=1, col=1,
                                )

                                fig_bp.add_trace(
                                    go.Scatter(
                                        x=df_bp_valid["Date"],
                                        y=df_bp_valid["Diastolic BP"],
                                        mode="lines+markers",
                                        name="Diastolic",
                                        line=dict(color="#53565A", width=3),
                                        marker=dict(size=8, color="#53565A"),
                                        hovertemplate="<b>Date:</b> %{x|%Y-%m-%d}<br><b>Diastolic:</b> %{y} mmHg<extra></extra>",
                                    ),
                                    row=2, col=1,
                                )

                                if len(df_bp_valid) >= 2:
                                    try:
                                        import numpy as np
                                        x_numeric = np.arange(len(df_bp_valid))
                                        degree = min(2, len(df_bp_valid) - 1)

                                        sys_vals = df_bp_valid["Systolic BP"].astype(float).to_numpy()
                                        sys_trend = np.poly1d(np.polyfit(x_numeric, sys_vals, degree))(x_numeric)
                                        fig_bp.add_trace(
                                            go.Scatter(
                                                x=df_bp_valid["Date"],
                                                y=sys_trend,
                                                mode="lines",
                                                name="Systolic Trend",
                                                line=dict(color="#007CB0", width=2, dash="dash"),
                                                hovertemplate="<b>Systolic trend:</b> %{y:.0f} mmHg<extra></extra>",
                                            ),
                                            row=1, col=1,
                                        )

                                        dia_vals = df_bp_valid["Diastolic BP"].astype(float).to_numpy()
                                        dia_trend = np.poly1d(np.polyfit(x_numeric, dia_vals, degree))(x_numeric)
                                        fig_bp.add_trace(
                                            go.Scatter(
                                                x=df_bp_valid["Date"],
                                                y=dia_trend,
                                                mode="lines",
                                                name="Diastolic Trend",
                                                line=dict(color="#53565A", width=2, dash="dash"),
                                                hovertemplate="<b>Diastolic trend:</b> %{y:.0f} mmHg<extra></extra>",
                                            ),
                                            row=2, col=1,
                                        )
                                    except Exception as e:
                                        logger.warning(f"Could not calculate BP trend: {e}")

                                x0 = df_bp_valid["Date"].min()
                                x1 = df_bp_valid["Date"].max()
                                tick0_year = (x0.year // 5) * 5
                                tick0 = f"{tick0_year}-01-01"

                                # Ensure y-ranges include zone bounds so bands can't clip out
                                sys_zone_min = min(v[0] for v in sys_bp_zones.values())
                                sys_zone_max = max(v[1] for v in sys_bp_zones.values())
                                dia_zone_min = min(v[0] for v in dia_bp_zones.values())
                                dia_zone_max = max(v[1] for v in dia_bp_zones.values())

                                sys_range = [
                                    min(sys_y_range_bp[0], sys_zone_min),
                                    max(sys_y_range_bp[1], sys_zone_max),
                                ]
                                dia_range = [
                                    min(dia_y_range_bp[0], dia_zone_min),
                                    max(dia_y_range_bp[1], dia_zone_max),
                                ]

                                fig_bp.update_layout(
                                    height=300,
                                    margin=dict(l=0, r=0, t=0, b=0),
                                    hovermode="x unified",
                                    plot_bgcolor="rgba(0,0,0,0)",
                                    paper_bgcolor="rgba(0,0,0,0)",
                                    legend=dict(
                                        orientation="h",
                                        yanchor="bottom",
                                        y=1.02,
                                        xanchor="right",
                                        x=1
                                    ),
                                    xaxis=dict(
                                        showgrid=True,
                                        gridwidth=1,
                                        gridcolor="rgba(128,128,128,0.3)",
                                    ),
                                    xaxis2=dict(
                                        showgrid=True,
                                        gridwidth=1,
                                        gridcolor="rgba(128,128,128,0.3)",
                                    ),
                                )

                                fig_bp.update_yaxes(
                                    title_text="Systolic (mmHg)",
                                    showgrid=True,
                                    gridwidth=1,
                                    gridcolor="rgba(128,128,128,0.3)",
                                    range=sys_range,
                                    row=1, col=1,
                                )

                                fig_bp.update_yaxes(
                                    title_text="Diastolic (mmHg)",
                                    showgrid=True,
                                    gridwidth=1,
                                    gridcolor="rgba(128,128,128,0.3)",
                                    range=dia_range,
                                    row=2, col=1,
                                )

                                st.plotly_chart(fig_bp, width="stretch")
                        else:
                            st.info("No BP data available")

                    with col3:
                        st.write("**Heart Rate Trend**")
                        if "Heart Rate (bpm)" in df.columns:
                            # Filter out None values for trend line calculation
                            df_hr_valid = df[["Date", "Heart Rate (bpm)"]].dropna()
                            
                            # Check if we have any numeric data
                            if len(df_hr_valid) == 0:
                                st.info("No numeric heart rate data available")
                            else:
                                # Create interactive Plotly chart for heart rate
                                fig_hr = go.Figure()
                                
                                # Add reference zones for Heart Rate - 5-ZONE SYMMETRICAL SYSTEM (red-orange-green-orange-red)
                                # Using age-specific zones from hr_zones dictionary
                                # Zone 1: Severe Bradycardia/Low (RED)
                                fig_hr.add_hrect(
                                    y0=hr_zones["severe_low"][0], y1=hr_zones["severe_low"][1],
                                    fillcolor="rgba(220, 53, 69, 0.2)",
                                    layer="below",
                                    line_width=0
                                )
                                
                                # Zone 2: Mild Bradycardia/Low (ORANGE - matches legend)
                                fig_hr.add_hrect(
                                    y0=hr_zones["mild_low"][0], y1=hr_zones["mild_low"][1],
                                    fillcolor="rgba(237, 139, 0, 0.2)",  # #ED8B00 - MODERATE_COLOR
                                    layer="below",
                                    line_width=0
                                )
                                
                                # Zone 3: Normal HR (GREEN) - Age-specific range
                                fig_hr.add_hrect(
                                    y0=hr_zones["normal"][0], y1=hr_zones["normal"][1],
                                    fillcolor="rgba(134, 188, 37, 0.25)",
                                    layer="below",
                                    line_width=0
                                )
                                
                                # Zone 4: Mild Tachycardia/High (ORANGE - matches legend)
                                fig_hr.add_hrect(
                                    y0=hr_zones["mild_high"][0], y1=hr_zones["mild_high"][1],
                                    fillcolor="rgba(237, 139, 0, 0.2)",  # #ED8B00 - MODERATE_COLOR
                                    layer="below",
                                    line_width=0
                                )
                                
                                # Zone 5: Significant Tachycardia/High (RED)
                                fig_hr.add_hrect(
                                    y0=hr_zones["severe_high"][0], y1=hr_zones["severe_high"][1],
                                    fillcolor="rgba(220, 53, 69, 0.2)",
                                    layer="below",
                                    line_width=0
                                )
                                
                                # Add data points and line
                                fig_hr.add_trace(go.Scatter(
                                    x=df_hr_valid["Date"],
                                    y=df_hr_valid["Heart Rate (bpm)"],
                                    mode='lines+markers',
                                    name='Heart Rate',
                                    line=dict(color='#002D72', width=3),  # Navy blue for better distinction
                                    marker=dict(size=8, color='#002D72'),
                                    hovertemplate='<b>Date:</b> %{x|%Y-%m-%d}<br><b>Heart Rate:</b> %{y} bpm<extra></extra>'
                                ))
                                
                                # Add trend line if we have at least 2 points with valid numeric values
                                if len(df_hr_valid) >= 2:
                                    try:
                                        import numpy as np
                                        # Convert dates to numeric for trend calculation
                                        x_numeric = np.arange(len(df_hr_valid))
                                        y_values = df_hr_valid["Heart Rate (bpm)"].values
                                        
                                        # Ensure all y_values are numeric
                                        y_values = np.array([float(y) for y in y_values])
                                        
                                        # Calculate polynomial trend (degree 2 for curve, or 1 for linear if only 2 points)
                                        degree = min(2, len(df_hr_valid) - 1)
                                        z = np.polyfit(x_numeric, y_values, degree)
                                        p = np.poly1d(z)
                                        trend_y = p(x_numeric)
                                        
                                        fig_hr.add_trace(go.Scatter(
                                            x=df_hr_valid["Date"],
                                            y=trend_y,
                                            mode='lines',
                                            name='Trend',
                                            line=dict(color='#002D72', width=2, dash='dash'),  # Navy blue
                                            hovertemplate='<b>Trend:</b> %{y:.0f} bpm<extra></extra>'
                                        ))
                                    except (ValueError, TypeError) as e:
                                        logger.warning(f"Could not calculate heart rate trend: {e}")
                                
                                fig_hr.update_layout(
                                    height=300,
                                    margin=dict(l=0, r=0, t=0, b=0),
                                    hovermode='x unified',
                                    plot_bgcolor='rgba(0,0,0,0)',
                                    paper_bgcolor='rgba(0,0,0,0)',
                                    legend=dict(
                                        orientation="h",
                                        yanchor="bottom",
                                        y=1.02,
                                        xanchor="right",
                                        x=1
                                    ),
                                    xaxis=dict(
                                        showgrid=True,
                                        gridwidth=1,
                                        gridcolor='rgba(128,128,128,0.3)'
                                    ),
                                    yaxis=dict(
                                        showgrid=True,
                                        gridwidth=1,
                                        gridcolor='rgba(128,128,128,0.3)',
                                        title='Heart Rate (bpm)',
                                        range=y_range_hr  # Age-specific Y-axis range
                                    )
                                )
                                st.plotly_chart(fig_hr, width='stretch')
                        else:
                            st.info("No heart rate data available")
                    
                    # Add common legend for all charts
                    st.markdown("""
                    <div style='text-align: center; padding: 0.5rem 0; margin-top: 0.5rem; font-size: 0.9rem; color: #5F5F5F;'>
                        <strong>Legend:</strong> &nbsp;
                        <span style='color: #DC3545;'>●</span> Critically Low/High &nbsp;|&nbsp;
                        <span style='color: #ED8B00;'>●</span> Mildly Low/High &nbsp;|&nbsp;
                        <span style='color: #86BC25;'>●</span> Ideal/Normal Range
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Add vitals summary below the charts
                    vitals_summary = health_overview.get("vitals_summary")
                    if vitals_summary:
                        st.markdown("---")
                        st.markdown("### 📊 Vitals Analysis")
                        render_vitals_summary(vitals_summary)  # Use vitals-specific risk level
                else:
                    st.info("No trend data available.")
            else:
                st.info("No vitals trend data available.")
        # =========================================================
        # SECTION: MEDICAL HISTORY (5-Category Display)
        # =========================================================
        elif section == "Medical History":
            
            # ========== SECTION 1: 📋 MEDICAL HISTORY (CONDITIONS) ==========
            st.markdown("### 📋 Medical History - Conditions")
            st.caption("Medical conditions categorized by risk level and chronicity")
            
            # Get categorized conditions from enhanced summary
            high_risk_conditions_data = data.get("high_risk_conditions", [])
            chronic_conditions_data = data.get("chronic_conditions", [])
            other_conditions_data = data.get("other_conditions", [])
            coded_conditions = data.get("coded_conditions", [])  # Keep for bbox lookups
            
            # Helper function to group conditions hierarchically
            def group_conditions_hierarchically(conditions):
                """
                Group conditions by hierarchical relationships.
                Returns: dict where key=parent_code, value={"parent": {...}, "children": [{...}]}
                Only includes conditions with evidence_boxes (from coded_conditions).
                """
                # Filter to only conditions with evidence_boxes
                conditions_with_evidence = [c for c in conditions if c.get("evidence_boxes")]
                
                groups = {}
                standalone = []
                
                for cond in conditions_with_evidence:
                    hierarchy_depth = cond.get("hierarchy_depth", 0)
                    hierarchical_path = cond.get("hierarchical_path", [])
                    snomed_code = cond.get("snomed_code", "")
                    
                    # If no hierarchy info, it's standalone
                    if not hierarchical_path or hierarchy_depth == 0:
                        standalone.append(cond)
                        continue
                    
                    # If depth 1, it's a parent (root node)
                    if hierarchy_depth == 1:
                        if snomed_code not in groups:
                            groups[snomed_code] = {"parent": cond, "children": []}
                    # If depth > 1, it's a child - find its parent
                    elif hierarchy_depth > 1 and len(hierarchical_path) > 0:
                        parent_code = hierarchical_path[0][0]  # First node in path is parent
                        if parent_code not in groups:
                            # Parent not in list yet, add as standalone for now
                            groups[parent_code] = {"parent": None, "children": [cond]}
                        else:
                            groups[parent_code]["children"].append(cond)
                
                return groups, standalone
            
            # Helper function to display a condition
            def display_condition(cond, idx, key_prefix, show_risk_badge=True):
                condition_name_raw = cond.get("condition", "Unknown")
                condition_name = to_camel_title(condition_name_raw)
                condition_type = cond.get("condition_type", "")
                icd10_code = cond.get("icd10_code") or cond.get("icd10", "")
                snomed_code = cond.get("snomed_code") or cond.get("snomed", "")
                # Backend provides evidence_text field for categorized conditions
                description = cond.get("summary") or cond.get("description") or cond.get("evidence_text", "")
                date = cond.get("date") or cond.get("date_reported", "")
                page = cond.get("page") or cond.get("page_number")
                evidence = cond.get("evidence") or cond.get("evidence_text", "")
                
                # Display condition with risk badge
                if show_risk_badge and condition_type:
                    if condition_type == "High Risk":
                        st.markdown(f"🔴 **{idx + 1}. {condition_name}**")
                    elif condition_type == "Chronic":
                        st.markdown(f"🟡 **{idx + 1}. {condition_name}**")
                    else:
                        st.markdown(f"🟢 **{idx + 1}. {condition_name}**")
                else:
                    st.markdown(f"**{idx + 1}. {condition_name}**")
                
                # Show description if available
                if description and description != condition_name:
                    st.caption(f"📝 {description}")
                elif evidence:
                    st.caption(f"📝 {evidence[:200]}")
                
                # Show codes and date in columns
                col1, col2, col3 = st.columns(3)
                with col1:
                    if icd10_code and str(icd10_code) not in ["N/A", "null", "None", ""]:
                        st.caption(f"**ICD-10:** {icd10_code}")
                with col2:
                    if snomed_code and str(snomed_code) not in ["N/A", "null", "None", ""]:
                        st.caption(f"**SNOMED:** {snomed_code}")
                with col3:
                    if date and str(date) not in ["", "N/A", "null", "None"]:
                        st.caption(f"📅 {date}")
                
                # Add clickable page button
                if page:
                    bbox_lookups = data.get("bbox_lookups", {})
                    medical_history_boxes = bbox_lookups.get("medical_history_boxes", [])
                    merged_bboxes = merge_bbox_from_both_sources(
                        condition_name,
                        coded_conditions,
                        medical_history_boxes
                    )
                    evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                    
                    if st.button(f"📄 View on Page {page}", key=f"{key_prefix}_{idx}_page_{page}", type="secondary"):
                        navigate_to_page_with_condition(page, evidence_dict)
                        st.rerun()
                
                st.divider()
            
            # Helper function to display hierarchical group
            def display_hierarchical_group(parent, children, idx, key_prefix):
                """Display a parent condition with its children in a hierarchical format"""
                # Display parent
                parent_name = parent.get("condition", "Unknown")
                parent_snomed = parent.get("snomed_code", "")
                parent_path = parent.get("path_display", parent_name)
                
                # Count total evidence across parent and children
                total_evidence = len(parent.get("evidence_boxes", []))
                all_pages = [parent.get("page")] if parent.get("page") else []
                
                for child in children:
                    total_evidence += len(child.get("evidence_boxes", []))
                    if child.get("page"):
                        all_pages.append(child.get("page"))
                
                unique_pages = sorted(set(p for p in all_pages if p))
                
                # Display parent node
                st.markdown(f"### 📍 {parent_name}")
                st.caption(f"**Path:** {parent_path}")
                st.caption(f"**SNOMED:** {parent_snomed} | **Evidence:** {total_evidence} bounding boxes across {len(unique_pages)} pages")
                
                # Show parent description
                parent_desc = parent.get("description") or parent.get("summary") or parent.get("evidence_text", "")
                if parent_desc:
                    with st.expander("📝 Clinical Summary", expanded=False):
                        st.write(parent_desc)
                
                # Display parent button
                if parent.get("page"):
                    bbox_lookups = data.get("bbox_lookups", {})
                    medical_history_boxes = bbox_lookups.get("medical_history_boxes", [])
                    merged_bboxes = merge_bbox_from_both_sources(
                        parent_name,
                        coded_conditions,
                        medical_history_boxes
                    )
                    evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                    
                    if st.button(f"📄 View Parent on Page {parent.get('page')}", key=f"{key_prefix}_parent_{idx}", type="secondary"):
                        navigate_to_page_with_condition(parent.get('page'), evidence_dict)
                        st.rerun()
                
                # Display children (indented)
                if children:
                    st.markdown(f"**└─ Related Findings ({len(children)}):**")
                    for child_idx, child in enumerate(children):
                        child_name = child.get("condition", "Unknown")
                        child_snomed = child.get("snomed_code", "")
                        child_date = child.get("date") or child.get("date_reported", "")
                        child_page = child.get("page")
                        child_evidence_count = len(child.get("evidence_boxes", []))
                        
                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;**{child_idx + 1}. {child_name}**")
                        st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;**SNOMED:** {child_snomed} | **Evidence:** {child_evidence_count} bbox | **Page:** {child_page} | **Date:** {child_date}")
                        
                        # Child view button
                        if child_page:
                            bbox_lookups = data.get("bbox_lookups", {})
                            medical_history_boxes = bbox_lookups.get("medical_history_boxes", [])
                            merged_bboxes = merge_bbox_from_both_sources(
                                child_name,
                                coded_conditions,
                                medical_history_boxes
                            )
                            evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                            
                            if st.button(f"📄 View Child on Page {child_page}", key=f"{key_prefix}_child_{idx}_{child_idx}", type="secondary"):
                                navigate_to_page_with_condition(child_page, evidence_dict)
                                st.rerun()
                
                st.divider()
            
            # Display High Risk Conditions with Hierarchical Grouping
            if high_risk_conditions_data:
                # Group hierarchically
                hierarchical_groups, standalone = group_conditions_hierarchically(high_risk_conditions_data)
                
                # Count total items (only those with evidence)
                total_with_evidence = len([c for c in high_risk_conditions_data if c.get("evidence_boxes")])
                
                with st.expander(f"🔴 **High Risk Conditions** ({total_with_evidence} items with evidence)", expanded=True):
                    # Display hierarchical groups
                    # IMPORTANT: In Medical History, show only children when both parent and child exist
                    # (children are more specific findings). Show parent only if no children.
                    group_idx = 0
                    for parent_code, group in hierarchical_groups.items():
                        if group["parent"] and group["children"]:
                            # Both parent and children exist - show ONLY children (more specific)
                            for child in group["children"]:
                                display_condition(child, group_idx, "hr_cond", show_risk_badge=False)
                                group_idx += 1
                        elif group["parent"] and not group["children"]:
                            # Only parent exists (no children) - show the parent
                            display_condition(group["parent"], group_idx, "hr_cond", show_risk_badge=False)
                            group_idx += 1
                        else:
                            # Children without parent in list - show children
                            for child in group["children"]:
                                display_condition(child, group_idx, "hr_cond", show_risk_badge=False)
                                group_idx += 1
                    
                    # Display standalone conditions
                    for cond in standalone:
                        display_condition(cond, group_idx, "hr_cond", show_risk_badge=False)
                        group_idx += 1
            else:
                st.info("✅ No high-risk conditions identified.")
            
            st.markdown("")  # Spacing
            
            # Display Chronic Conditions with umbrella grouping
            chronic_conditions_grouped = data.get("chronic_conditions_grouped", {})
            # Determine which data to display and count
            chronic_display_data = chronic_conditions_data
            if isinstance(chronic_conditions_grouped, list) and chronic_conditions_grouped:
                chronic_display_data = chronic_conditions_grouped
            
            if chronic_display_data:
                with st.expander(f"🟡 **Chronic Conditions** ({len(chronic_display_data)} items)", expanded=True):
                    # Check if we have hierarchical grouped data (dict with "grouped" key)
                    if isinstance(chronic_conditions_grouped, dict) and chronic_conditions_grouped.get("grouped"):
                        grouped_items = chronic_conditions_grouped["grouped"]
                        ungrouped_items = chronic_conditions_grouped.get("ungrouped", [])
                        
                        # Display grouped items first (by umbrella category)
                        for category_key, category_data in grouped_items.items():
                            category_name = category_data.get("name", "Unknown Category")
                            category_icon = category_data.get("icon", "📋")
                            category_items = category_data.get("items", [])
                            
                            # Create nested expander for each umbrella category
                            with st.expander(f"{category_icon} **{category_name}** ({len(category_items)} items)", expanded=False):
                                for idx, cond in enumerate(category_items):
                                    display_condition(cond, idx, f"chronic_grouped_{category_key}", show_risk_badge=False)
                        
                        # Display ungrouped items
                        if ungrouped_items:
                            st.markdown("---")
                            st.markdown(f"**Other Chronic Conditions** ({len(ungrouped_items)} items)")
                            for idx, cond in enumerate(ungrouped_items):
                                display_condition(cond, idx, "chronic_ungrouped", show_risk_badge=False)
                    else:
                        # Fallback to flat display (works for both list and non-grouped dict)
                        for idx, cond in enumerate(chronic_display_data):
                            display_condition(cond, idx, "chronic_cond", show_risk_badge=False)
            
            st.markdown("")  # Spacing
            
            # Display Other Conditions with umbrella grouping
            other_conditions_grouped = data.get("other_conditions_grouped", {})
            # Determine which data to display and count
            other_display_data = other_conditions_data
            if isinstance(other_conditions_grouped, list) and other_conditions_grouped:
                other_display_data = other_conditions_grouped
            
            if other_display_data:
                with st.expander(f"🟢 **Other Medical Findings** ({len(other_display_data)} items)", expanded=False):
                    # Check if we have hierarchical grouped data (dict with "grouped" key)
                    if isinstance(other_conditions_grouped, dict) and other_conditions_grouped.get("grouped"):
                        grouped_items = other_conditions_grouped["grouped"]
                        ungrouped_items = other_conditions_grouped.get("ungrouped", [])
                        
                        # Display grouped items first
                        for category_key, category_data in grouped_items.items():
                            category_name = category_data.get("name", "Unknown Category")
                            category_icon = category_data.get("icon", "📋")
                            category_items = category_data.get("items", [])
                            
                            # Create nested expander for each umbrella category
                            with st.expander(f"{category_icon} **{category_name}** ({len(category_items)} items)", expanded=False):
                                for idx, cond in enumerate(category_items):
                                    display_condition(cond, idx, f"other_grouped_{category_key}", show_risk_badge=False)
                        
                        # Display ungrouped items
                        if ungrouped_items:
                            st.markdown("---")
                            st.markdown(f"**Miscellaneous Findings** ({len(ungrouped_items)} items)")
                            for idx, cond in enumerate(ungrouped_items):
                                display_condition(cond, idx, "other_ungrouped", show_risk_badge=False)
                    else:
                        # Fallback to flat display (works for both list and non-grouped dict)
                        for idx, cond in enumerate(other_display_data):
                            display_condition(cond, idx, "other_cond", show_risk_badge=False)
            
            st.markdown("")  # Add spacing
            
            # ========== SECTION 2: 💊 MEDICATIONS ==========
            st.markdown("### 💊 Medications")
            st.caption("Medications categorized by risk level")
            
            # Get categorized medications from enhanced summary
            high_risk_medications_data = data.get("high_risk_medications", [])
            other_medications_data = data.get("other_medications", [])
            
            # Helper function to display a medication
            def display_medication(med, idx, key_prefix, show_risk_badge=True):
                medication_name = med.get("condition", "Unknown")
                rxnorm_code = med.get("rxnorm_code") or med.get("rxnorm", "")
                # Backend provides evidence_text field for categorized medications
                description = med.get("summary") or med.get("description") or med.get("evidence_text", "")
                date = med.get("date") or med.get("date_reported", "")
                page = med.get("page") or med.get("page_number")
                evidence = med.get("evidence") or med.get("evidence_text", "")
                
                # Display medication with risk badge
                if show_risk_badge:
                    st.markdown(f"🔴 **{idx + 1}. {medication_name.upper()}**")
                else:
                    st.markdown(f"**{idx + 1}. {medication_name.upper()}**")
                
                # Show description if available
                if description and description != medication_name:
                    st.caption(f"📝 {description}")
                elif evidence:
                    st.caption(f"📝 {evidence[:200]}")
                
                # Show codes and date in columns
                col1, col2 = st.columns(2)
                with col1:
                    if rxnorm_code and str(rxnorm_code) not in ["N/A", "null", "None", ""]:
                        st.caption(f"**RxNorm:** {rxnorm_code}")
                with col2:
                    if date and str(date) not in ["", "N/A", "null", "None"]:
                        st.caption(f"📅 {date}")
                
                # Add clickable page button
                if page:
                    bbox_lookups = data.get("bbox_lookups", {})
                    medication_history_boxes = bbox_lookups.get("medication_history_boxes", [])
                    merged_bboxes = merge_bbox_from_both_sources(
                        medication_name,
                        coded_conditions,
                        medication_history_boxes
                    )
                    evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                    
                    if st.button(f"📄 View on Page {page}", key=f"{key_prefix}_{idx}_page_{page}", type="secondary"):
                        navigate_to_page_with_condition(page, evidence_dict)
                        st.rerun()
                
                st.divider()
            
            # Display High Risk Medications
            if high_risk_medications_data:
                with st.expander(f"🔴 **High Risk Medications** ({len(high_risk_medications_data)} items)", expanded=True):
                    for idx, med in enumerate(high_risk_medications_data):
                        display_medication(med, idx, "hr_med", show_risk_badge=False)
            else:
                st.info("✅ No high-risk medications identified.")
            
            st.markdown("")  # Spacing
            
            # Display Other Medications with umbrella grouping
            other_medications_grouped = data.get("other_medications_grouped", {})
            # Determine which data to display and count
            other_meds_display_data = other_medications_data
            if isinstance(other_medications_grouped, list) and other_medications_grouped:
                other_meds_display_data = other_medications_grouped
            
            if other_meds_display_data:
                with st.expander(f"💊 **Other Medications** ({len(other_meds_display_data)} items)", expanded=False):
                    # Check if we have hierarchical grouped data (dict with "grouped" key)
                    if isinstance(other_medications_grouped, dict) and other_medications_grouped.get("grouped"):
                        grouped_items = other_medications_grouped["grouped"]
                        ungrouped_items = other_medications_grouped.get("ungrouped", [])
                        
                        # Display grouped items first (by medication class)
                        for category_key, category_data in grouped_items.items():
                            category_name = category_data.get("name", "Unknown Category")
                            category_icon = category_data.get("icon", "💊")
                            category_items = category_data.get("items", [])
                            
                            # Create nested expander for each medication class
                            with st.expander(f"{category_icon} **{category_name}** ({len(category_items)} items)", expanded=False):
                                for idx, med in enumerate(category_items):
                                    display_medication(med, idx, f"other_grouped_{category_key}", show_risk_badge=False)
                        
                        # Display ungrouped medications
                        if ungrouped_items:
                            st.markdown("---")
                            st.markdown(f"**Miscellaneous Medications** ({len(ungrouped_items)} items)")
                            for idx, med in enumerate(ungrouped_items):
                                display_medication(med, idx, "other_med_ungrouped", show_risk_badge=False)
                    else:
                        # Fallback to flat display (works for both list and non-grouped dict)
                        for idx, med in enumerate(other_meds_display_data):
                            display_medication(med, idx, "other_med", show_risk_badge=False)
            
            st.markdown("")  # Add spacing
            
            # ========== SECTION 3: 🏥 CLINICAL EVENTS ==========
            st.markdown("### 🏥 Clinical Events")
            
            # Get bbox lookups for surgeries and hospitalizations
            bbox_lookups = data.get("bbox_lookups", {})
            surgeries_boxes = bbox_lookups.get("surgeries_boxes", [])
            hospitalizations_boxes = bbox_lookups.get("hospitalizations_boxes", [])
            coded_conditions = data.get("coded_conditions", [])
            
            # Display Surgeries as 4-column table
            st.markdown("**Surgeries**")
            surgeries_list = data.get("surgeries", [])
            
            if surgeries_list and len(surgeries_list) > 0:
                st.caption(f"Showing {len(surgeries_list)} surgical procedures")
                
                # Table header
                col1, col2, col3, col4 = st.columns([1, 2, 3, 1])
                with col1:
                    st.markdown("**Date**")
                with col2:
                    st.markdown("**Procedure**")
                with col3:
                    st.markdown("**Summary**")
                with col4:
                    st.markdown("**Source**")
                
                st.markdown("---")
                
                # Display each surgery
                for idx, surgery in enumerate(surgeries_list):
                    date = surgery.get("date", "N/A") if isinstance(surgery, dict) else "N/A"
                    procedure = surgery.get("procedure", "Unknown procedure") if isinstance(surgery, dict) else str(surgery)
                    summary = surgery.get("summary", "") if isinstance(surgery, dict) else ""
                    
                    col1, col2, col3, col4 = st.columns([1, 2, 3, 1])
                    
                    with col1:
                        st.write(f"**{date}**")
                    
                    with col2:
                        st.write(procedure)
                    
                    with col3:
                        st.write(summary if summary else "—")
                    
                    with col4:
                        # Find matching bbox for page link
                        merged_bboxes = merge_bbox_from_both_sources(
                            procedure,
                            coded_conditions,
                            surgeries_boxes
                        )
                        
                        if merged_bboxes:
                            pages = sorted(merged_bboxes.keys())
                            evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                            first_page = pages[0]
                            
                            if st.button(f"📄 P.{first_page}", key=f"surgery_link_{idx}", type="secondary"):
                                navigate_to_page_with_condition(first_page, evidence_dict)
                                st.rerun()
                        else:
                            st.write("—")
                    
                    # Add subtle divider between rows
                    if idx < len(surgeries_list) - 1:
                        st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #e0e0e0;'>", unsafe_allow_html=True)
            
            else:
                st.caption("None recorded")
            
            st.markdown("")  # Spacing
            
            # Display Hospitalizations as 4-column table
            st.markdown("**Hospitalizations**")
            hospitalizations_list = data.get("hospitalizations", [])
            
            if hospitalizations_list and len(hospitalizations_list) > 0:
                st.caption(f"Showing {len(hospitalizations_list)} hospitalizations")
                
                # Table header
                col1, col2, col3, col4 = st.columns([1, 2, 3, 1])
                with col1:
                    st.markdown("**Date**")
                with col2:
                    st.markdown("**Reason**")
                with col3:
                    st.markdown("**Summary**")
                with col4:
                    st.markdown("**Source**")
                
                st.markdown("---")
                
                # Display each hospitalization
                for idx, hosp in enumerate(hospitalizations_list):
                    date = hosp.get("date", "N/A") if isinstance(hosp, dict) else "N/A"
                    reason = hosp.get("reason", "Unknown reason") if isinstance(hosp, dict) else str(hosp)
                    summary = hosp.get("summary", "") if isinstance(hosp, dict) else ""
                    
                    col1, col2, col3, col4 = st.columns([1, 2, 3, 1])
                    
                    with col1:
                        st.write(f"**{date}**")
                    
                    with col2:
                        st.write(reason)
                    
                    with col3:
                        st.write(summary if summary else "—")
                    
                    with col4:
                        # Find matching bbox for page link
                        merged_bboxes = merge_bbox_from_both_sources(
                            reason,
                            coded_conditions,
                            hospitalizations_boxes
                        )
                        
                        if merged_bboxes:
                            pages = sorted(merged_bboxes.keys())
                            evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                            first_page = pages[0]
                            
                            if st.button(f"📄 P.{first_page}", key=f"hosp_link_{idx}", type="secondary"):
                                navigate_to_page_with_condition(first_page, evidence_dict)
                                st.rerun()
                        else:
                            st.write("—")
                    
                    # Add subtle divider between rows
                    if idx < len(hospitalizations_list) - 1:
                        st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #e0e0e0;'>", unsafe_allow_html=True)
            
            else:
                st.caption("None recorded")
            
            st.markdown("")  # Add spacing
            
            # ========== SECTION 4: 📅 MEDICAL VISITS TIMELINE ==========
            st.markdown("### 📅 Medical Visits Timeline")
            
            # Get medical appointments from medical_summary
            medical_appointments = data.get("medical_appointments", [])
            
            # Get medical_appointments_boxes for bbox highlighting
            bbox_lookups = data.get("bbox_lookups", {})
            medical_appointments_boxes = bbox_lookups.get("medical_appointments_boxes", [])
            
            if medical_appointments:
                import re
                from datetime import datetime
                
                # Process each appointment
                visits = []
                for appt_idx, appt_item in enumerate(medical_appointments):
                    # Initialize variables for this appointment
                    appt_date = None
                    appt_encounter = ""
                    appt_description = ""
                    page_ref = None
                    appt_bbox = None
                    
                    # Handle both dict format (enriched) and list format (raw [[date, encounter, description]])
                    if isinstance(appt_item, dict):
                        # Enriched format has 'date', 'encounter', 'description', 'page_reference', 'appointment_bbox'
                        appt_date = appt_item.get('date')
                        appt_encounter = appt_item.get('encounter', '')
                        appt_description = appt_item.get('description', '') or appt_item.get('appointment', '')
                        page_ref = appt_item.get('page_reference')
                        appt_bbox = appt_item.get('appointment_bbox')
                        
                        # Legacy support: if no separate 'date' field, try parsing from 'appointment' string
                        if not appt_date and appt_description:
                            appt_str = str(appt_description)
                            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', appt_str)
                            appt_date = date_match.group(1) if date_match else None
                            # If we parsed date from string, remove it from description
                            if appt_date:
                                appt_description = re.sub(r'^\d{4}-\d{2}-\d{2}:\s*', '', appt_str).strip()
                                # Also strip status if present
                                appt_description = re.sub(r'\s*\([^)]+\)\s*$', '', appt_description).strip()
                    
                    elif isinstance(appt_item, list) and len(appt_item) >= 3:
                        # New format: [date, exact_quote, summary]
                        appt_date = appt_item[0]
                        appt_encounter = appt_item[1]  # Exact encounter header
                        appt_description = appt_item[2]  # Visit summary
                        page_ref = None
                        appt_bbox = None
                    
                    elif isinstance(appt_item, list) and len(appt_item) == 2:
                        # Fallback format: [date, description]
                        appt_date = appt_item[0]
                        appt_encounter = appt_item[1]
                        appt_description = ""  # No summary available
                        page_ref = None
                        appt_bbox = None
                    
                    elif isinstance(appt_item, str):
                        # Legacy string format: "YYYY-MM-DD: Description (status)"
                        appt_str = appt_item
                        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', appt_str)
                        appt_date = date_match.group(1) if date_match else None
                        
                        # Remove date prefix and status suffix - goes in encounter field
                        appt_encounter = re.sub(r'^\d{4}-\d{2}-\d{2}:\s*', '', appt_str).strip()
                        appt_encounter = re.sub(r'\s*\([^)]+\)\s*$', '', appt_encounter).strip()
                        appt_description = ""  # No separate summary for legacy format
                        
                        page_ref = None
                        appt_bbox = None
                    else:
                        # Skip invalid entries
                        continue
                    
                    # Ensure we have at least an encounter or description
                    if not appt_encounter and not appt_description:
                        continue
                    
                    # Use the pre-enriched bbox if available
                    appointment_bboxes = {}
                    
                    if appt_bbox and page_ref:
                        # We already have the exact bbox for this appointment from the enrichment phase
                        bbox = appt_bbox.get("bbox")
                        line_id = appt_bbox.get("line_id")
                        
                        if bbox:
                            if page_ref not in appointment_bboxes:
                                appointment_bboxes[page_ref] = []
                            
                            bbox_with_id = bbox.copy() if isinstance(bbox, dict) else bbox
                            if isinstance(bbox_with_id, dict) and line_id:
                                bbox_with_id["_line_id"] = line_id
                            appointment_bboxes[page_ref].append(bbox_with_id)
                    
                    # Get encounter header from the parsed data
                    # For dict format, use 'encounter' field; for lists, appt_encounter was already set
                    if isinstance(appt_item, dict):
                        encounter_header = appt_item.get('encounter', '')
                    elif isinstance(appt_item, list) and len(appt_item) >= 2:
                        encounter_header = appt_encounter  # Set in elif branches above
                    elif isinstance(appt_item, str):
                        encounter_header = appt_encounter  # Set in elif branch above
                    else:
                        encounter_header = ""
                    
                    # Create visit entry with safe date parsing
                    try:
                        if appt_date and appt_date not in ["Not specified", "No date", ""]:
                            sort_date = datetime.strptime(appt_date, "%Y-%m-%d")
                        else:
                            sort_date = datetime(9999, 1, 1)  # Far future for sorting
                    except (ValueError, TypeError):
                        sort_date = datetime(9999, 1, 1)  # Far future for invalid dates
                    
                    visits.append({
                        "date": appt_date if appt_date else "No date",
                        "encounter": encounter_header,
                        "description": appt_description,
                        "bboxes": appointment_bboxes,
                        "sort_date": sort_date
                    })
                
                # Sort by date (most recent first, pending at end)
                visits_sorted = sorted(visits, key=lambda x: x["sort_date"], reverse=True)
                
                st.caption(f"Showing all {len(visits_sorted)} medical visits (most recent first)")
                
                # Table header
                col1, col2, col3, col4 = st.columns([1, 2, 3, 1])
                with col1:
                    st.markdown("**Date**")
                with col2:
                    st.markdown("**Encounter**")
                with col3:
                    st.markdown("**Description**")
                with col4:
                    st.markdown("**Source**")
                
                st.markdown("---")
                
                # Display as 4-column table: Date | Encounter | Description | Link
                for idx, visit in enumerate(visits_sorted):
                    date_display = visit["date"]
                    encounter_header = visit["encounter"]
                    description = visit["description"]
                    
                    # Create 4-column layout: Date | Encounter | Description | Link
                    col1, col2, col3, col4 = st.columns([1, 2, 3, 1])
                    
                    with col1:
                        st.write(f"**{date_display}**")
                    
                    with col2:
                        st.write(f"*{encounter_header}*" if encounter_header else "—")
                    
                    with col3:
                        st.write(description if description else "—")
                    
                    with col4:
                        # Add page link if we found matching bbox
                        if visit["bboxes"]:
                            pages = sorted(visit["bboxes"].keys())
                            evidence_dict = create_evidence_dict_from_bboxes(visit["bboxes"])
                            first_page = pages[0]
                            
                            if st.button(f"📄 Page {first_page}", key=f"visit_link_{idx}", type="secondary"):
                                navigate_to_page_with_condition(first_page, evidence_dict)
                                st.rerun()
                        else:
                            st.write("—")  # Show dash if no link available
                    
                    # Add subtle divider between rows
                    if idx < len(visits_sorted) - 1:
                        st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #e0e0e0;'>", unsafe_allow_html=True)
            else:
                st.info("No medical visits recorded.")
        
        # =========================================================
        # SECTION: FAMILY HISTORY
        # =========================================================
        elif section == "Family History":
            
            st.subheader("Family History")
            
            family_history = data.get("family_history", [])
            
            if family_history:
                # Get bbox lookups for family history
                bbox_lookups = data.get("bbox_lookups", {})
                family_history_boxes = bbox_lookups.get("family_history_boxes", [])
                coded_conditions = data.get("coded_conditions", [])
                
                st.caption(f"Showing {len(family_history)} family history entries")
                
                for idx, entry in enumerate(family_history):
                    relation = entry.get("relation", "N/A")
                    condition = entry.get("condition", "N/A")
                    diagnosis_age = entry.get("diagnosis_age")
                    notes = entry.get("notes")
                    
                    # Display as numbered list item (NOT H3 header)
                    st.markdown(f"**{idx + 1}. {relation}: {condition}**")
                    
                    # Show additional details if available
                    details = []
                    if diagnosis_age:
                        details.append(f"Diagnosed at age {diagnosis_age}")
                    if notes:
                        details.append(f"Notes: {notes}")
                    
                    if details:
                        for detail in details:
                            st.caption(f"   {detail}")
                    
                    # Add clickable page link with bbox highlighting
                    page_ref = entry.get("page_reference")
                    if page_ref:
                        # Try to merge bbox for this condition/relation
                        search_text = f"{relation} {condition}".lower()
                        merged_bboxes = merge_bbox_from_both_sources(
                            search_text,
                            coded_conditions,
                            family_history_boxes
                        )
                        
                        if merged_bboxes:
                            evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                            if st.button(f"→ View on Page {page_ref}", key=f"family_{idx}_page_{page_ref}", type="secondary"):
                                navigate_to_page_with_condition(page_ref, evidence_dict)
                                st.rerun()
                        else:
                            # No bbox, just navigate to page
                            if st.button(f"→ Go to Page {page_ref}", key=f"family_{idx}_page_{page_ref}", type="secondary"):
                                st.session_state.pdf_page = page_ref
                                st.session_state.highlight_bbox = None
                                st.rerun()
                    
                    # Add subtle divider between entries
                    if idx < len(family_history) - 1:
                        st.markdown("<hr style='margin: 12px 0; border: none; border-top: 1px solid #e0e0e0;'>", unsafe_allow_html=True)
                    
            else:
                st.info("No family history records found.")
        
        # =========================================================
        # SECTION: DIAGNOSTIC TESTS & IMAGES (Show ALL, not just abnormal)
        # =========================================================
        elif section == "Diagnostic Images":
            
            st.subheader("All Diagnostic Tests & Images")
            st.caption("All diagnostic tests are shown below. Abnormal findings are highlighted.")
            
            diagnostic_images = data.get("diagnostic_images", [])
            
            if diagnostic_images:
                # Separate abnormal and normal for better organization
                abnormal_tests = [img for img in diagnostic_images if img.get("analysis_result") == "likely_abnormal"]
                normal_tests = [img for img in diagnostic_images if img.get("analysis_result") != "likely_abnormal"]
                
                # Show abnormal tests first
                if abnormal_tests:
                    st.markdown("### ⚠️ Abnormal Findings")
                    for idx, img in enumerate(abnormal_tests):
                        img_type = img.get("type", "N/A")
                        finding = img.get("finding", "N/A")
                        page_ref = img.get("page_reference", None)
                        image_path = img.get("image_path", None)
                        
                        logger.info(f"Displaying abnormal diagnostic image {idx + 1}: type={img_type}, page={page_ref}, image_path={image_path}")
                        
                        # Highlight abnormal with red background
                        st.markdown(f"""<div style='background-color: #ffe6e6; padding: 1rem; border-radius: 5px; border-left: 5px solid #cc0000; margin-bottom: 1rem;'>
                    <h4>🔴 {idx + 1}. {img_type}</h4>
                    </div>""", unsafe_allow_html=True)
                        
                        # Display the actual image from S3 if available
                        if image_path:
                            try:
                                # Extract S3 key from path (remove s3://bucket/ prefix)
                                if image_path.startswith("s3://"):
                                    # Format: s3://bucket/key/path
                                    s3_key = "/".join(image_path.split("/")[3:])
                                else:
                                    # Already just the key
                                    s3_key = image_path
                                
                                # Fetch image from S3
                                logger.info(f"Fetching image from S3 - Bucket: {BUCKET_NAME}, Key: {s3_key}")
                                response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
                                image_bytes = response['Body'].read()
                                
                                # Display image
                                st.image(image_bytes, width="stretch", caption=f"{img_type} - Page {page_ref or 'Unknown'}")
                                logger.info(f"Successfully displayed image: {s3_key}")
                            except Exception as e:
                                logger.error(f"Error loading image from S3 bucket={BUCKET_NAME}, key={s3_key}: {e}")
                                st.warning(f"⚠️ Image not available: {image_path}")
                        else:
                            logger.warning(f"No image_path found for diagnostic image {idx + 1}")
                        
                        st.write(f"**Finding:** {finding}")
                        
                        if img.get("analysis"):
                            st.write(f"**Analysis:** {img['analysis']}")
                        
                        if img.get("test_date"):
                            st.caption(f"📅 Test Date: {img['test_date']}")
                        
                        if page_ref:
                            st.caption(f"📄 Source: Page {page_ref}")
                            # Add clickable button to navigate to page
                            if st.button(f"→ View Page {page_ref}", key=f"diagnostic_img_abnormal_{idx}_page_{page_ref}", type="secondary"):
                                st.session_state.pdf_page = page_ref
                                st.session_state.highlight_bbox = None
                                st.rerun()
                                    
                # Show normal tests
                if normal_tests:
                    st.markdown("### ✅ Normal/Other Test Results")
                    for idx, img in enumerate(normal_tests):
                        img_type = img.get("type", "N/A")
                        finding = img.get("finding", "N/A")
                        page_ref = img.get("page_reference", None)
                        image_path = img.get("image_path", None)
                        
                        logger.info(f"Displaying normal diagnostic image {idx + 1}: type={img_type}, page={page_ref}, image_path={image_path}")
                        
                        st.markdown(f"#### {idx + 1}. {img_type}")
                        
                        # Display the actual image from S3 if available
                        if image_path:
                            try:
                                # Extract S3 key from path (remove s3://bucket/ prefix)
                                if image_path.startswith("s3://"):
                                    # Format: s3://bucket/key/path
                                    s3_key = "/".join(image_path.split("/")[3:])
                                else:
                                    # Already just the key
                                    s3_key = image_path
                                
                                # Fetch image from S3
                                logger.info(f"Fetching image from S3 - Bucket: {BUCKET_NAME}, Key: {s3_key}")
                                response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
                                image_bytes = response['Body'].read()
                                
                                # Display image
                                st.image(image_bytes, width="stretch", caption=f"{img_type} - Page {page_ref or 'Unknown'}")
                                logger.info(f"Successfully displayed image: {s3_key}")
                            except Exception as e:
                                logger.error(f"Error loading image from S3 bucket={BUCKET_NAME}, key={s3_key}: {e}")
                                st.warning(f"⚠️ Image not available: {image_path}")
                        else:
                            logger.warning(f"No image_path found for diagnostic image {idx + 1}")
                        
                        st.write(f"**Finding:** {finding}")
                        
                        if img.get("analysis"):
                            st.write(f"**Analysis:** {img['analysis']}")
                        
                        if img.get("test_date"):
                            st.caption(f"📅 Test Date: {img['test_date']}")
                        
                        if page_ref:
                            st.caption(f"📄 Source: Page {page_ref}")
                            # Add clickable button to navigate to page
                            if st.button(f"→ View Page {page_ref}", key=f"diagnostic_img_normal_{idx}_page_{page_ref}", type="secondary"):
                                st.session_state.pdf_page = page_ref
                                st.session_state.highlight_bbox = None
                                st.rerun()
                        
            else:
                st.info("No diagnostic tests or images detected in the APS document.")
        
        # =========================================================
        # SECTION: LAB RESULTS
        # =========================================================
        elif section == "Lab Results":
            
            st.subheader("Lab Results")
            
            lab_results = data.get("lab_results", [])
            lab_results_summary = data.get("lab_results_summary", "")
            lab_results_keywords = data.get("lab_results_keywords", {"high": [], "moderate": [], "low": []})
            lab_results_risk_level = data.get("lab_results_risk_level", "moderate")
            
            if lab_results:
                st.caption(f"Showing {len(lab_results)} lab test results")
                
                # Display summary at the top if available with color coding using lab-specific keywords
                if lab_results_summary:
                    render_colored_summary(
                        lab_results_summary, 
                        title="Summary:", 
                        show_bullets=True,
                        high_risk_terms=lab_results_keywords.get("high", []),
                        moderate_risk_terms=lab_results_keywords.get("moderate", []),
                        low_risk_terms=lab_results_keywords.get("low", []),
                        risk_level=lab_results_risk_level  # Use lab-specific risk level for border color
                    )
                    st.markdown("")  # Add spacing
                
                # Get bbox lookups for lab results
                bbox_lookups = data.get("bbox_lookups", {})
                lab_results_boxes = bbox_lookups.get("lab_results_boxes", [])
                coded_conditions = data.get("coded_conditions", [])
                
                # Header row for table-like layout
                header_col1, header_col2, header_col3, header_col4, header_col5 = st.columns([2.5, 2, 1.5, 1, 1.5])
                with header_col1:
                    st.markdown("**Test Name**")
                with header_col2:
                    st.markdown("**Value / Range**")
                with header_col3:
                    st.markdown("**Status**")
                with header_col4:
                    st.markdown("**Date**")
                with header_col5:
                    st.markdown("**Source**")
                
                st.markdown("---")
                
                # Display each lab result with page links (table-like layout)
                for idx, lab in enumerate(lab_results):
                    test_name = lab.get("test", lab.get("test_name", "Unknown Test"))
                    value = lab.get("value", "N/A")
                    normal_range = lab.get("normal_range", "N/A")
                    risk = lab.get("risk", lab.get("status", ""))
                    date = lab.get("date", "N/A")
                    page_ref = lab.get("page_reference", lab.get("page"))
                    
                    # If no page_ref, try to find one by searching lab_results_boxes
                    if not page_ref and lab_results_boxes:
                        merged_bboxes = find_lab_result_bboxes(
                            test_name,
                            value,
                            lab_results_boxes,
                            coded_conditions
                        )
                        if merged_bboxes:
                            # Get the first page from merged results
                            pages = list(merged_bboxes.keys())
                            if pages:
                                page_ref = pages[0]
                                logger.debug(f"Found page reference {page_ref} for lab result: {test_name}")
                    
                    # Determine risk styling
                    is_abnormal = any(keyword in str(risk).lower() for keyword in ["high", "abnormal", "elevated", "low"])
                    risk_emoji = "🔴" if is_abnormal else "🟢"
                    
                    # Table-like display: single row with 5 columns including page link
                    col1, col2, col3, col4, col5 = st.columns([2.5, 2, 1.5, 1, 1.5])
                    
                    with col1:
                        st.markdown(f"{risk_emoji} {test_name}")
                    
                    with col2:
                        info_parts = []
                        if value != "N/A":
                            info_parts.append(f"**{value}**")
                        if normal_range != "N/A":
                            info_parts.append(f"(Normal: {normal_range})")
                        if info_parts:
                            st.caption(" ".join(info_parts))
                        else:
                            st.caption("—")
                    
                    with col3:
                        if risk:
                            risk_color = "red" if is_abnormal else "green"
                            st.caption(f":{risk_color}[{risk}]")
                        else:
                            st.caption("—")
                    
                    with col4:
                        if date != "N/A":
                            st.caption(f"{date}")
                        else:
                            st.caption("—")
                    
                    with col5:
                        # Page link in the same row (table column)
                        if page_ref:
                            # Use specialized function for lab results bbox matching
                            # This searches for both test name and value in lab_results_boxes
                            merged_bboxes = find_lab_result_bboxes(
                                test_name,
                                value,
                                lab_results_boxes,
                                coded_conditions
                            )
                            
                            if merged_bboxes:
                                evidence_dict = create_evidence_dict_from_bboxes(merged_bboxes)
                                bbox_count = sum(len(bboxes) for bboxes in merged_bboxes.values())
                                logger.debug(f"Lab result '{test_name}' matched {bbox_count} bbox(es) on page {page_ref}")
                                if st.button(f"📄 P.{page_ref}", key=f"lab_{idx}_page_{page_ref}", type="secondary"):
                                    navigate_to_page_with_condition(page_ref, evidence_dict)
                                    st.rerun()
                            else:
                                # No bbox, just navigate to page
                                logger.debug(f"Lab result '{test_name}' has page ref {page_ref} but no bbox found")
                                if st.button(f"📄 P.{page_ref}", key=f"lab_{idx}_page_{page_ref}", type="secondary"):
                                    st.session_state.pdf_page = page_ref
                                    st.session_state.highlight_bbox = None
                                    st.rerun()
                        else:
                            st.caption("—")
                            logger.debug(f"Lab result '{test_name}' has no page reference")
                    
                    # Thin separator between lab results
                    if idx < len(lab_results) - 1:
                        st.markdown("")
                    
            else:
                st.info("No lab results found in the document.")
    
    # =========================================================
    # RIGHT COLUMN: PDF VIEWER (Only if visible)
    # =========================================================
    if st.session_state.pdf_viewer_visible:
        with pdf_col:
            st.markdown("### 📄 Document Viewer")
            st.markdown("---")
            
            # Check if PDF is loaded
            if st.session_state.pdf_bytes:
                # Get current page and highlighting info
                current_page = st.session_state.pdf_page if st.session_state.pdf_page is not None else 1
                highlight_bboxes = st.session_state.highlight_bbox if st.session_state.highlight_bbox else []
                
                # Get total page count
                total_pages = get_pdf_page_count(st.session_state.pdf_bytes)
                
                if total_pages > 0:
                    # Navigation controls
                    nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])
                    
                    with nav_col1:
                        if st.button("⬅️", key="pdf_prev_right", disabled=(current_page is None or current_page <= 1)):
                            st.session_state.pdf_page = max(1, current_page - 1)
                            st.session_state.highlight_bbox = None
                            st.rerun()
                    
                    with nav_col2:
                        st.markdown(f"<div style='text-align: center; margin-top: 8px;'>Page {current_page}/{total_pages}</div>", unsafe_allow_html=True)
                    
                    with nav_col3:
                        if st.button("➡️", key="pdf_next_right", disabled=(current_page is None or current_page >= total_pages)):
                            st.session_state.pdf_page = min(total_pages, current_page + 1)
                            st.session_state.highlight_bbox = None
                            st.rerun()
                    
                    # Render the PDF page with highlights
                    try:
                        page_image = render_pdf_page_with_highlights(
                            st.session_state.pdf_bytes,
                            current_page,
                            highlight_bboxes,
                            zoom=2.0  # Higher zoom for right column
                        )
                        if page_image:
                            st.image(page_image, width='stretch')
                            if highlight_bboxes:
                                st.caption(f"🔴 {len(highlight_bboxes)} highlighted region(s) on this page.")
                        else:
                            st.warning(f"Could not render page {current_page}.")
                    except Exception as e:
                        st.error(f"Error rendering PDF.")
                        logger.error(f"PDF rendering error: {e}")
                else:
                    st.error("The PDF could not be loaded or has no pages.")
            else:
                st.info("The PDF will be displayed here once the dashboard is generated.")

else:
    st.info("🚀 Upload a PDF to get started!")

# =========================================================
# FOOTER
# =========================================================
# Footer with base64 encoded SVG
import base64
try:
    with open("deloitte_BIG.svg", "rb") as f:
        svg_data = base64.b64encode(f.read()).decode()
    footer_logo_src = f'data:image/svg+xml;base64,{svg_data}'
except:
    footer_logo_src = 'deloitte_BIG.png'

st.markdown("""
<hr style="margin: 1.5rem 0 0.5rem 0; border: none; border-top: 1px solid #E0E0E0;"/>
<div style='text-align: center; padding: 0.4rem 0;'>
    <img src='{}' style='height: 24px; width: auto; margin-bottom: 0.4rem;' alt='Deloitte'/>
    <div style='color: #5F5F5F; font-size: 0.6rem;'>
        APS Risk Assessment Platform v1.0<br/>
        <span style='font-size: 0.52rem;'>Session ID: {}</span>
    </div>
</div>
""".format(footer_logo_src, st.session_state.session_id if st.session_state.session_id else 'Not started'), unsafe_allow_html=True)

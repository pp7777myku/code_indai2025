import os
import fastapi
import uvicorn
import csv
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request, HTTPException, Form, File, UploadFile  # Added Form, File, UploadFile
# Pydantic BaseModel no longer needed for this endpoint if using Form parameters
import requests
import io
import logging
from typing import Optional  # For optional UploadFile

# Set proxy (can be commented out if not needed)
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"


# --- 日志配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 环境变量和 API 密钥配置 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    logger.error("Missing Gemini API Key. Please set GEMINI_API_KEY environment variable.")
    raise Exception("Missing Gemini API Key. Please set GEMINI_API_KEY environment variable.")
try:
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Gemini API: {e}")
    raise

# --- 常量定义 ---
CASES_CSV_URL = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQb49fI2IgWq1sa_Lbh6wlq4RZor8lNX6OgBN1MXX3fQ2YxnWIL4EN_6TmhtJE_YXDZKT00WzLz7b7h/pub?gid=104964265&single=true&output=csv'
ERROR_MSG_KB_LOAD_FAILED = "Error: Knowledge base could not be loaded. Please check the source or network."
ERROR_MSG_KB_PROCESS_FAILED = "Error: Knowledge base could not be processed. Please check the CSV format or headers."
ERROR_MSG_GEMINI_FAILED = "Error: Failed to get a response from the AI model. Please try again later."
ERROR_MSG_INPUT_VALIDATION = "Error: Invalid input. Please ensure all fields are correctly filled."
ERROR_MSG_FILE_PROCESS = "Error: Could not process the uploaded file."

# --- FastAPI 应用和模板 ---
app = fastapi.FastAPI()
templates = Jinja2Templates(directory="templates")


# Pydantic模型 ChatRequest 不再被 /chat 端点直接使用，因为我们改用了 Form 参数

def load_and_serialize_cases() -> str:
    # ... (此函数与上一版本相同，保持不变) ...
    cases = []
    logger.info(f"Attempting to load knowledge base from: {CASES_CSV_URL}")
    try:
        response = requests.get(CASES_CSV_URL, timeout=10)
        response.raise_for_status()

        csv_content = response.text
        if csv_content.startswith('\ufeff'):
            csv_content = csv_content[1:]
            logger.info("BOM detected and removed from CSV content.")

        csvfile = io.StringIO(csv_content)
        reader = csv.DictReader(csvfile)

        if reader.fieldnames:
            logger.info(f"Detected CSV headers: {reader.fieldnames}")
        else:
            logger.warning(
                "CSV DictReader could not detect any field names (headers). The CSV might be empty or severely malformed.")
            return ERROR_MSG_KB_PROCESS_FAILED

        col_equipment = "Equipment Name"
        col_fault_type = "Fault Type"
        col_symptom = "Fault Symptom"
        col_causes = "Possible Causes"
        col_diag_steps = "Diagnostic Steps"
        col_solution = "Solution"

        for i, row in enumerate(reader):
            equipment_name = row.get(col_equipment, '').strip()
            fault_type = row.get(col_fault_type, '').strip()
            symptom = row.get(col_symptom, '').strip()
            possible_causes = row.get(col_causes, '').strip()
            diagnostic_steps = row.get(col_diag_steps, '').strip()
            solution = row.get(col_solution, '').strip()

            if equipment_name and symptom and solution:
                case_string = (
                    f"Equipment: {equipment_name}; "
                    f"Fault Type: {fault_type}; "
                    f"Symptom: {symptom}; "
                    f"Possible Causes: {possible_causes}; "
                    f"Diagnostic Steps: {diagnostic_steps}; "
                    f"Solution: {solution}"
                )
                cases.append(case_string)
            else:
                logger.warning(
                    f"Row {i + 1} in CSV is missing one or more critical fields "
                    f"({col_equipment}, {col_symptom}, {col_solution}) or fields are empty, and will be skipped. "
                )  # Removed detailed row data log for brevity, can be re-added if needed

        if not cases:
            logger.warning(
                "Knowledge base loaded but it's empty or no valid cases found after processing rows with new structure.")
        else:
            logger.info(
                f"Successfully loaded and serialized {len(cases)} cases from the knowledge base using new structure.")
        return '\n'.join(cases)

    except requests.exceptions.Timeout:  # Simplified error handling for brevity
        logger.error(f"Timeout fetching CSV: {CASES_CSV_URL}")
        return ERROR_MSG_KB_LOAD_FAILED
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching CSV: {CASES_CSV_URL}. Error: {e}")
        return ERROR_MSG_KB_LOAD_FAILED
    except csv.Error as e:
        logger.error(f"CSV formatting error: {e}", exc_info=True)
        return ERROR_MSG_KB_PROCESS_FAILED
    except Exception as e:
        logger.error(f"Unexpected error in load_and_serialize_cases: {e}", exc_info=True)
        return ERROR_MSG_KB_PROCESS_FAILED


async def call_gemini_with_explanation(
        system_prompt_from_user: str,
        equipment_name: str,
        fault_symptom: str,
        uploaded_file: Optional[UploadFile] = None
) -> (str, str):
    """Send prompt (and optional file) to Gemini, return process + final result"""

    serialized_cases = load_and_serialize_cases()
    if serialized_cases == ERROR_MSG_KB_LOAD_FAILED or serialized_cases == ERROR_MSG_KB_PROCESS_FAILED:
        logger.warning("Skipping Gemini call due to knowledge base loading/processing failure.")
        return ERROR_MSG_KB_LOAD_FAILED, "Knowledge base issue."

    # --- 准备发送给 Gemini 的内容 ---
    content_parts_for_gemini = []

    # 1. 主文本提示 (包含系统指令、知识库、用户文本输入)
    main_prompt_text = f"""{system_prompt_from_user}

**Related Knowledge Base Information (from CSV):**
{serialized_cases if serialized_cases else "No knowledge base entries loaded or available for reference."}

Now, please diagnose the following fault based on the user's text input AND any attached file (if provided).
Equipment: {equipment_name}
Fault Symptom: {fault_symptom}
"""
    if uploaded_file:
        main_prompt_text += f"\nAn additional file named '{uploaded_file.filename}' (type: {uploaded_file.content_type}) has been uploaded. Please consider its content in your diagnosis."

    main_prompt_text += "\n\nAnswer:"
    content_parts_for_gemini.append(main_prompt_text)

    # 2. 上传的文件 (如果存在)
    file_data_part = None
    if uploaded_file:
        try:
            logger.info(
                f"Processing uploaded file: {uploaded_file.filename}, type: {uploaded_file.content_type}, size: {uploaded_file.size}")
            file_bytes = await uploaded_file.read()
            if uploaded_file.content_type and file_bytes:  # Make sure we have content and type
                # Gemini generally supports image/*, audio/*, video/*, text/*
                # For other types, it might or might not work as expected.
                # It's safer for common image types.
                if uploaded_file.content_type.startswith("image/") or \
                        uploaded_file.content_type.startswith("text/") or \
                        uploaded_file.content_type == "application/pdf":  # Add other supported types as needed
                    file_data_part = {
                        "mime_type": uploaded_file.content_type,
                        "data": file_bytes
                    }
                    content_parts_for_gemini.append(file_data_part)
                    logger.info(f"Added file '{uploaded_file.filename}' to Gemini request.")
                else:
                    logger.warning(
                        f"Uploaded file type '{uploaded_file.content_type}' may not be directly supported by Gemini for rich parsing. Passing as is.")
                    # Still attempt to pass it, Gemini might handle some generic types or ignore.
                    # Alternatively, one could try to extract text if it's e.g. a docx, etc.
                    file_data_part = {  # Try passing it anyway
                        "mime_type": uploaded_file.content_type,
                        "data": file_bytes
                    }
                    content_parts_for_gemini.append(file_data_part)


        except Exception as e:
            logger.error(f"Error reading or processing uploaded file {uploaded_file.filename}: {e}", exc_info=True)
            # Return an error or just proceed without the file? For now, proceed without.
            # The main_prompt_text already mentions the file name, so Gemini is aware a file was intended.
            content_parts_for_gemini.append(
                f"\n[System Note: There was an issue processing the uploaded file '{uploaded_file.filename}'. Please proceed with text-based diagnosis if possible.]")

    logger.info(f"Sending request to Gemini API with {len(content_parts_for_gemini)} parts.")
    if len(content_parts_for_gemini) > 1 and file_data_part:
        logger.info(
            f"Content part 0 (text) length: {len(content_parts_for_gemini[0])} chars. Part 1 (file) MIME type: {file_data_part.get('mime_type')}")
    else:
        logger.info(
            f"Content part 0 (text) length: {len(content_parts_for_gemini[0])} chars. No file part or file processing error.")

    try:
        # Use a model that supports multimodal input, like gemini-1.5-flash or gemini-1.5-pro
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(content_parts_for_gemini)  # Send list of parts
        full_text = response.text.strip()
        logger.info("Successfully received response from Gemini API.")

        explanation_part = full_text
        solution_part = "No specific solution/control action parsed from AI response."

        split_keywords = ["Solution:", "Решение:", "Control Action:"]
        found_split = False
        for keyword in split_keywords:
            if keyword in full_text:
                parts = full_text.split(keyword, 1)
                explanation_part = parts[0].strip()
                if len(parts) > 1:
                    solution_part = parts[1].strip()
                found_split = True
                logger.info(f"Response split by keyword: '{keyword}'")
                break

        if not found_split:
            logger.warning(
                "Could not find a clear 'Solution:' or 'Control Action:' keyword in Gemini's response to split.")

        return explanation_part, solution_part

    except google_exceptions.GoogleAPICallError as e:
        logger.error(f"Gemini API call failed: {e}", exc_info=True)
        # Check for specific permission errors or invalid argument due to file type
        if "User location is not supported" in str(e):
            return "Error: Gemini API access is not available for your current region.", ""
        if "API key not valid" in str(e):
            return "Error: Gemini API key is not valid. Please check your configuration.", ""
        return ERROR_MSG_GEMINI_FAILED, f"Details: {str(e)}"
    except Exception as e:
        logger.error(f"An unexpected error occurred while calling Gemini API or processing its response: {e}",
                     exc_info=True)
        return ERROR_MSG_GEMINI_FAILED, "An unexpected error occurred."


@app.get("/", response_class=fastapi.responses.HTMLResponse)
async def index(request: Request):
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception as e:
        logger.error(f"Could not serve index.html: {e}. Make sure 'templates/index.html' exists.")
        return fastapi.responses.HTMLResponse("<h1>Error</h1><p>Could not load the HTML template.</p>", status_code=500)


@app.post("/chat")
async def chat(
        system_prompt: str = Form(...),
        equipment: str = Form(...),
        symptoms: str = Form(...),
        fault_file: Optional[UploadFile] = File(None)  # File is now optional
):
    if not system_prompt or not equipment or not symptoms:  # Basic validation for text fields
        logger.warning(f"Chat request validation failed: Missing text fields.")
        raise HTTPException(status_code=422, detail=ERROR_MSG_INPUT_VALIDATION)

    logger.info(
        f"Received chat request. Equipment: {equipment[:50]}. File uploaded: {fault_file.filename if fault_file else 'No'}")

    explanation, control_action = await call_gemini_with_explanation(  # Added await
        system_prompt,
        equipment,
        symptoms,
        fault_file
    )

    if explanation == ERROR_MSG_KB_LOAD_FAILED or \
            explanation == ERROR_MSG_KB_PROCESS_FAILED or \
            explanation == ERROR_MSG_GEMINI_FAILED or \
            explanation.startswith("Error: Gemini API access is not available") or \
            explanation.startswith("Error: Gemini API key is not valid"):
        logger.warning(
            f"Chat request processing resulted in an error. Explanation: {explanation}, Details: {control_action}")
        status_code = 503 if explanation in [ERROR_MSG_KB_LOAD_FAILED, ERROR_MSG_KB_PROCESS_FAILED] else 500
        if explanation.startswith("Error: Gemini API access is not available") or explanation.startswith(
                "Error: Gemini API key is not valid"):
            status_code = 403  # Forbidden
        return JSONResponse(
            status_code=status_code,
            content={"error": explanation, "details": control_action}
        )

    return JSONResponse(content={
        "explanation": explanation,
        "control_action": control_action
    })


if __name__ == "__main__":
    if not os.path.exists("templates"):
        os.makedirs("templates")
        logger.info("Created 'templates' directory.")
    if not os.path.exists("templates/index.html"):
        with open("templates/index.html", "w", encoding="utf-8") as f:
            # You should place the HTML content provided above into this file.
            f.write(
                "<h1>FastAPI Backend Running</h1><p>Please ensure 'templates/index.html' contains the correct UI.</p>")
        logger.info("Created a placeholder templates/index.html file. Please replace with the full HTML.")

    uvicorn.run(app, host="localhost", port=8000)
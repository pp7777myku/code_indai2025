import os
import fastapi
import uvicorn
import csv
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request, HTTPException, Form, File, UploadFile
import requests
import io
import logging
from typing import Optional, List

# --- 代理设置 (如果不需要，请保持注释状态) ---
# os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
# os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
# logger.info(f"Using HTTPS_PROXY: {os.getenv('HTTPS_PROXY')}") # 可选: 记录代理设置
# logger.info(f"Using HTTP_PROXY: {os.getenv('HTTP_PROXY')}")   # 可选: 记录代理设置


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
ERROR_MSG_KB_LOAD_FAILED = "Ошибка: Не удалось загрузить базу знаний. Проверьте источник или сеть."
ERROR_MSG_KB_PROCESS_FAILED = "Ошибка: Не удалось обработать базу знаний. Проверьте формат CSV или заголовки."
ERROR_MSG_GEMINI_FAILED = "Ошибка: Не удалось получить ответ от AI модели. Пожалуйста, попробуйте позже."
ERROR_MSG_INPUT_VALIDATION = "Ошибка: Неверные входные данные. Убедитесь, что все поля заполнены корректно."
ERROR_MSG_FILE_PROCESS = "Ошибка: Не удалось обработать один или несколько загруженных файлов."

# 文件上传限制
MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOWED_MIME_TYPES = [
    "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif",  # 图片
    "application/pdf",  # PDF
    "text/plain", "text/markdown", "text/csv",  # 文本
    "audio/wav", "audio/mp3", "audio/ogg", "audio/flac",  # 音频
    "video/mp4", "video/webm", "video/mov",  # 视频
]

# --- FastAPI 应用和模板 ---
app = fastapi.FastAPI()
templates = Jinja2Templates(directory="templates")


def load_and_serialize_cases() -> str:
    cases = []
    logger.info(f"Attempting to load knowledge base from: {CASES_CSV_URL}")
    try:
        # 检查是否设置了代理，并相应地使用它们
        proxies = {}
        http_proxy = os.getenv('HTTP_PROXY')
        https_proxy = os.getenv('HTTPS_PROXY')
        if http_proxy:
            proxies['http'] = http_proxy
        if https_proxy:
            proxies['https'] = https_proxy

        if proxies:
            logger.info(f"Using proxies for request: {proxies}")
            response = requests.get(CASES_CSV_URL, timeout=10, proxies=proxies)
        else:
            response = requests.get(CASES_CSV_URL, timeout=10)

        response.raise_for_status()
        csv_content = response.text
        if csv_content.startswith('\ufeff'):
            csv_content = csv_content[1:]
            logger.info("BOM detected and removed from CSV content.")
        csvfile = io.StringIO(csv_content)
        reader = csv.DictReader(csvfile)
        if not reader.fieldnames:
            logger.warning(
                "CSV DictReader could not detect any field names. CSV might be empty or malformed at header level.")
            return ERROR_MSG_KB_PROCESS_FAILED
        logger.info(f"Detected CSV headers: {reader.fieldnames}")

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
                    f"Equipment: {equipment_name}; Fault Type: {fault_type}; Symptom: {symptom}; "
                    f"Possible Causes: {possible_causes}; Diagnostic Steps: {diagnostic_steps}; Solution: {solution}"
                )
                cases.append(case_string)
            else:
                logger.warning(
                    f"Row {i + 1} in CSV is missing critical fields ({col_equipment}, {col_symptom}, {col_solution}) or fields are empty, and will be skipped."
                )
        if not cases:
            logger.warning("Knowledge base loaded but no valid cases found after processing rows.")
        else:
            logger.info(f"Successfully loaded and serialized {len(cases)} cases from the knowledge base.")
        return '\n'.join(cases)
    except requests.exceptions.Timeout:
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
        uploaded_files: List[UploadFile] = []
) -> (str, str):
    serialized_cases = load_and_serialize_cases()
    if serialized_cases == ERROR_MSG_KB_LOAD_FAILED or serialized_cases == ERROR_MSG_KB_PROCESS_FAILED:
        logger.warning("Skipping Gemini call due to knowledge base loading/processing failure.")
        return ERROR_MSG_KB_LOAD_FAILED, "Проблема с базой знаний."

    content_parts_for_gemini = []
    processed_file_info = []

    main_prompt_text = f"""{system_prompt_from_user}

**Related Knowledge Base Information (from CSV):**
{serialized_cases if serialized_cases else "Нет загруженных или доступных для справки записей базы знаний."}

Now, please diagnose the following fault based on the user's text input AND any attached files (if provided).
Equipment: {equipment_name}
Fault Symptom: {fault_symptom}
"""
    if uploaded_files:
        valid_files_count = sum(1 for f in uploaded_files if f and f.filename)
        if valid_files_count > 0:
            filenames = [f.filename for f in uploaded_files if f and f.filename]
            main_prompt_text += f"\n{len(filenames)} дополнительный файл(ы) был(и) загружен(ы): {', '.join(filenames)}. Пожалуйста, учтите их содержимое в своем диагнозе."
        else:
            main_prompt_text += f"\nЗагружено 0 дополнительных файлов (или файлы были недействительны)."

    main_prompt_text += "\n\nAnswer:"
    content_parts_for_gemini.append(main_prompt_text)

    for uploaded_file in uploaded_files:
        if not (uploaded_file and uploaded_file.filename and uploaded_file.content_type and hasattr(uploaded_file,
                                                                                                    'size')):
            logger.info("Skipping an invalid or empty file upload entry.")
            continue
        try:
            logger.info(
                f"Processing uploaded file: {uploaded_file.filename}, type: {uploaded_file.content_type}, size: {uploaded_file.size}")
            # Серверная валидация здесь уже должна была быть пройдена в /chat эндпоинте
            # Но для полноты, если эта функция вызывается из другого места, можно добавить
            if uploaded_file.size > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    f"File '{uploaded_file.filename}' is too large ({uploaded_file.size} bytes), skipping for Gemini.")
                content_parts_for_gemini.append(
                    f"\n[Системное примечание: Файл '{uploaded_file.filename}' не был отправлен AI, так как он слишком большой.]")
                continue
            if uploaded_file.content_type not in ALLOWED_MIME_TYPES:
                logger.warning(
                    f"File '{uploaded_file.filename}' has unsupported MIME type ({uploaded_file.content_type}), skipping for Gemini.")
                content_parts_for_gemini.append(
                    f"\n[Системное примечание: Файл '{uploaded_file.filename}' с типом '{uploaded_file.content_type}' не был отправлен AI из-за неподдерживаемого формата.]")
                continue

            file_bytes = await uploaded_file.read()
            if file_bytes:
                content_parts_for_gemini.append({
                    "mime_type": uploaded_file.content_type,
                    "data": file_bytes
                })
                processed_file_info.append(f"'{uploaded_file.filename}' (type: {uploaded_file.content_type})")
                logger.info(f"Added file '{uploaded_file.filename}' to Gemini request parts.")
            else:
                logger.warning(f"Skipping file '{uploaded_file.filename}' due to empty content after read.")
        except Exception as e:
            logger.error(f"Error reading or processing uploaded file {uploaded_file.filename}: {e}", exc_info=True)
            content_parts_for_gemini.append(
                f"\n[Системное примечание: Произошла ошибка при обработке загруженного файла '{uploaded_file.filename}'. Он не может быть использован для анализа.]")

    logger.info(f"Sending request to Gemini API with {len(content_parts_for_gemini)} parts.")
    if processed_file_info:
        logger.info(f"Files processed for Gemini: {', '.join(processed_file_info)}")

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(content_parts_for_gemini)
        full_text = response.text.strip()
        logger.info("Successfully received response from Gemini API.")

        explanation_part = full_text
        solution_part = "Конкретное решение/действие по управлению не было извлечено из ответа AI."

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
        if "User location is not supported" in str(e):
            return "Ошибка: Доступ к Gemini API недоступен для вашего текущего региона.", ""
        if "API key not valid" in str(e):
            return "Ошибка: Ключ Gemini API недействителен. Пожалуйста, проверьте конфигурацию.", ""
        if "Invalid content" in str(e) or "Unsupported MimeType" in str(e) or "Invalid request" in str(
                e):  # "Invalid request" can happen for bad file data
            logger.error(
                f"Gemini API Error: Invalid or unsupported content/MIME type, or invalid request. Details: {e}")
            return ERROR_MSG_FILE_PROCESS, f"Gemini сообщил о проблеме с содержимым/типом файла или запросом: {str(e)}"
        return ERROR_MSG_GEMINI_FAILED, f"Детали: {str(e)}"
    except Exception as e:
        logger.error(f"An unexpected error occurred while calling Gemini API or processing its response: {e}",
                     exc_info=True)
        return ERROR_MSG_GEMINI_FAILED, "Произошла непредвиденная ошибка."


@app.get("/", response_class=fastapi.responses.HTMLResponse)
async def index(request: Request):
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception as e:
        logger.error(f"Could not serve index.html: {e}. Make sure 'templates/index.html' exists.")
        return fastapi.responses.HTMLResponse("<h1>Ошибка</h1><p>Не удалось загрузить HTML шаблон.</p>",
                                              status_code=500)


@app.post("/chat")
async def chat(
        system_prompt: str = Form(...),
        equipment: str = Form(...),
        symptoms: str = Form(...),
        fault_files: List[UploadFile] = File([])
):
    if not system_prompt or not equipment or not symptoms:
        logger.warning(f"Chat request validation failed: Missing text fields.")
        raise HTTPException(status_code=422, detail=ERROR_MSG_INPUT_VALIDATION)

    validated_files_for_gemini = []
    for uploaded_file in fault_files:
        if uploaded_file and uploaded_file.filename and hasattr(uploaded_file, 'size') and hasattr(uploaded_file,
                                                                                                   'content_type'):  # Basic check for a valid UploadFile object
            if uploaded_file.size == 0 and uploaded_file.filename:  # Some browsers might send empty file part if no file selected
                logger.info(f"Skipping empty file entry: {uploaded_file.filename}")
                continue

            if uploaded_file.size > MAX_FILE_SIZE_BYTES:
                logger.warning(f"File rejected (too large): {uploaded_file.filename}, size: {uploaded_file.size}")
                raise HTTPException(
                    status_code=413,
                    detail=f"Файл '{uploaded_file.filename}' ({round(uploaded_file.size / 1024 / 1024, 2)}MB) превышает лимит размера в {MAX_FILE_SIZE_MB}MB."
                )
            if uploaded_file.content_type not in ALLOWED_MIME_TYPES:
                logger.warning(
                    f"File rejected (unsupported type): {uploaded_file.filename}, type: {uploaded_file.content_type}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Тип файла '{uploaded_file.filename}' ({uploaded_file.content_type}) не поддерживается. Поддерживаемые типы: {', '.join(ALLOWED_MIME_TYPES)}"
                )
            validated_files_for_gemini.append(uploaded_file)
        elif uploaded_file and not uploaded_file.filename:  # Handle case where an empty file part might be sent
            logger.info("Skipping an empty file part without filename.")

    log_filenames = [f.filename for f in validated_files_for_gemini if
                     f.filename] if validated_files_for_gemini else "Нет файлов"
    logger.info(f"Received chat request. Equipment: {equipment[:50]}. Validated files for Gemini: {log_filenames}")

    explanation, control_action = await call_gemini_with_explanation(
        system_prompt,
        equipment,
        symptoms,
        validated_files_for_gemini
    )

    error_conditions = [
        ERROR_MSG_KB_LOAD_FAILED, ERROR_MSG_KB_PROCESS_FAILED,
        ERROR_MSG_GEMINI_FAILED, ERROR_MSG_FILE_PROCESS
    ]
    status_code = 200  # Default OK

    if explanation in error_conditions or \
            explanation.startswith("Ошибка: Доступ к Gemini API недоступен") or \
            explanation.startswith("Ошибка: Ключ Gemini API недействителен"):

        logger.warning(f"Chat request processing error. Explanation: {explanation}, Details: {control_action}")
        if explanation in [ERROR_MSG_KB_LOAD_FAILED, ERROR_MSG_KB_PROCESS_FAILED]:
            status_code = 503
        elif explanation.startswith("Ошибка: Доступ к Gemini API недоступен") or \
                explanation.startswith("Ошибка: Ключ Gemini API недействителен"):
            status_code = 403
        elif explanation == ERROR_MSG_FILE_PROCESS:
            status_code = 400
        else:
            status_code = 500

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
            f.write(
                "<h1>FastAPI Backend Running</h1><p>Please ensure 'templates/index.html' contains the correct UI.</p>")
        logger.info(
            "Created a placeholder templates/index.html file. Please replace with the full HTML provided in previous responses.")

    uvicorn.run(app, host="0.0.0.0", port=8000)
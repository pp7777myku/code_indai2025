import os
import fastapi
import uvicorn
import csv
import google.generativeai as genai
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request, Form

# Set proxy (can be commented out if not needed)
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"

# Read Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    raise Exception("Missing Gemini API Key. Please set GEMINI_API_KEY environment variable.")
genai.configure(api_key=GEMINI_API_KEY)

# Configure cases file path
CASES_FILE_PATH = 'cases.csv'

# Create FastAPI app
app = fastapi.FastAPI()

# Specify templates directory
templates = Jinja2Templates(directory="templates")


def load_and_serialize_cases() -> str:
    """Load cases and serialize them into text"""
    cases = []
    with open(CASES_FILE_PATH, mode='r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            trigger = row.get('Trigger', '').strip()
            example = row.get('Examples', '').strip()
            control_action = row.get('Control Action', '').strip()
            if trigger and example and control_action:
                cases.append(f"Trigger: {trigger}; Example: {example}; Control Action: {control_action}")
    return '\n'.join(cases)


def call_gemini_with_explanation(user_input: str) -> (str, str):
    """Send prompt to Gemini, return process + final result"""
    serialized_cases = load_and_serialize_cases()
    prompt = f"""
You are a senior industrial equipment maintenance expert and fault diagnosis engineer. Please accurately diagnose potential fault causes based on the equipment and fault symptoms provided by the user, **leveraging the "Related Knowledge Base Information" provided below**, and then provide detailed, actionable diagnostic steps and solutions.

**Related Knowledge Base Information:**
[This is where you will insert the most relevant fault cases, causes, diagnoses, and solutions retrieved from your CSV.]

Please strictly adhere to the following rules:
1.  **Reference Knowledge Base:** Prioritize using the "Related Knowledge Base Information" for diagnosis. If the knowledge base does not contain a direct match, infer based on your general professional knowledge.
2.  **Professionalism:** Use professional terminology from the industrial domain to ensure the accuracy and reliability of the diagnosis.
3.  **Detail:** List all possible fault causes, diagnostic steps, and repair measures as comprehensively as possible.
4.  **Structure:** Your response should be clearly divided into four sections: "Diagnosis Result," "Possible Causes," "Diagnostic Steps," and "Solution."
5.  **Ask for More Information:** If the information is insufficient for an accurate diagnosis, proactively ask the user for more details.
6.  **Avoid Guesswork:** If you cannot determine a definitive answer, state that the information is insufficient.

Now, please diagnose the following fault by Russian language:
Equipment: [User's input for equipment name]
Fault Symptom: [User's input for fault description]

Cases:
{serialized_cases}

User Request:
{user_input}

Answer:
"""
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(prompt)
    full_text = response.text.strip()

    # Parse out explanation and final action
    if "Control Action:" in full_text:
        explanation, control_action = full_text.split("Control Action:", 1)
        return explanation.strip(), control_action.strip()
    else:
        return "No clear explanation.", full_text


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Display home page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/chat", response_class=HTMLResponse)
async def chat(request: Request, user_input: str = Form(...)):
    """Handle user input"""
    explanation, control_action = call_gemini_with_explanation(user_input)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user_input": user_input,
        "explanation": explanation,
        "control_action": control_action
    })


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8000)
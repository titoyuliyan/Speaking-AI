from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import os
from datetime import datetime
import base64
import openai
import json
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import sqlite3

load_dotenv()

AUDIO_FOLDER = "audio"
DATABASE = "speaking.db"
MAX_AUDIO_SIZE = 25 * 1024 * 1024

os.makedirs(AUDIO_FOLDER, exist_ok=True)

openai.api_key = os.getenv("OPENAI_API_KEY")

if not openai.api_key:
    print("❌ ERROR: OPENAI_API_KEY not found!")
    exit(1)
else:
    print("✅ API Key loaded successfully")

# 10 Speaking Prompts
SPEAKING_PROMPTS = [
    "She sells fresh apples every Saturday morning at the market.",
    "The teacher checked the students' homework carefully after class ended.",
    "I usually read English books before going to sleep at night.",
    "My brother plays football with his friends after school every afternoon.",
    "They are planning to visit the museum next weekend together.",
    "Please close the door quietly when you leave the room please.",
    "The little boy is learning how to ride a bicycle alone.",
    "We discussed the problem and found a simple solution together.",
    "My favorite movie was released last year in cinemas worldwide.",
    "She smiled happily when she heard the good news yesterday."
]

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Main scores table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT NOT NULL,
            total_fluency REAL NOT NULL,
            total_pronunciation REAL NOT NULL,
            total_score REAL NOT NULL,
            overall_feedback TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Individual question scores
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS question_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            question_number INTEGER NOT NULL,
            prompt_text TEXT NOT NULL,
            fluency INTEGER NOT NULL,
            pronunciation INTEGER NOT NULL,
            score INTEGER NOT NULL,
            feedback TEXT,
            audio_file TEXT,
            FOREIGN KEY (test_id) REFERENCES student_tests (id)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

init_db()

app = Flask(__name__)
app.secret_key = os.urandom(24)

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def ai_score(audio_file_path, prompt_text):
    try:
        print("🎤 Transcribing audio...")
        
        with open(audio_file_path, "rb") as audio_file:
            transcript = openai.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
            text = transcript.text
        
        print(f"📝 Transcript: {text}")
        print("🤖 Analyzing with AI...")
        
        ai_prompt = f"""
You are an English speaking assessment AI.
The student was asked to read: "{prompt_text}"
The student said: "{text}"

Evaluate:
- Fluency: smoothness, natural flow, pacing (0-100)
- Pronunciation: clarity, accuracy of sounds (0-100)

Provide specific feedback in Indonesian about pronunciation errors and fluency issues.

Respond ONLY with valid JSON (no markdown):
{{
    "fluency": <int 0-100>,
    "pronunciation": <int 0-100>,
    "feedback": "<feedback in Indonesian>"
}}
"""
        
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":ai_prompt}],
            temperature=0.3
        )
        
        content = response.choices[0].message.content.strip()
        
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        
        result = json.loads(content)
        fluency = int(result.get("fluency", 50))
        pronunciation = int(result.get("pronunciation", 50))
        feedback = result.get("feedback", "No feedback available")
        score = round((fluency + pronunciation) / 2)
        
        print(f"✅ Score: Fluency={fluency}, Pronunciation={pronunciation}, Total={score}")
        
        return fluency, pronunciation, score, feedback
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return 0, 0, 0, f"Error: {str(e)}"

@app.route("/")
def index():
    session.clear()
    return render_template("test_start.html")

@app.route("/start-test", methods=["POST"])
def start_test():
    student_name = request.form.get("name", "").strip()
    
    if not student_name:
        flash("Please enter your name", "error")
        return redirect(url_for('index'))
    
    session['student_name'] = student_name
    session['current_question'] = 0
    session['question_data'] = []
    
    return redirect(url_for('question', num=1))

@app.route("/question/<int:num>")
def question(num):
    if 'student_name' not in session:
        return redirect(url_for('index'))
    
    if num < 1 or num > len(SPEAKING_PROMPTS):
        return redirect(url_for('index'))
    
    prompt = SPEAKING_PROMPTS[num - 1]
    total_questions = len(SPEAKING_PROMPTS)
    
    return render_template("question.html", 
                         question_num=num,
                         total_questions=total_questions,
                         prompt=prompt,
                         student_name=session['student_name'])

@app.route("/submit-answer", methods=["POST"])
def submit_answer():
    if 'student_name' not in session:
        return jsonify({"error": "Session expired"}), 400
    
    question_num = int(request.form.get("question_num"))
    audio_data = request.form.get("audio")
    
    if not audio_data:
        return jsonify({"error": "No audio data"}), 400
    
    try:
        # Save audio
        header_data, encoded = audio_data.split(",", 1)
        audio_bytes = base64.b64decode(encoded)
        
        safe_name = secure_filename(session['student_name'].replace(" ", "_"))
        filename = f"{safe_name}_q{question_num}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.webm"
        filepath = os.path.join(AUDIO_FOLDER, filename)
        
        with open(filepath, "wb") as f:
            f.write(audio_bytes)
        
        # Get AI score
        prompt_text = SPEAKING_PROMPTS[question_num - 1]
        fluency, pronunciation, score, feedback = ai_score(filepath, prompt_text)
        
        # Store in session
        if 'question_data' not in session:
            session['question_data'] = []
        
        session['question_data'].append({
            'question_num': question_num,
            'prompt': prompt_text,
            'fluency': fluency,
            'pronunciation': pronunciation,
            'score': score,
            'feedback': feedback,
            'audio_file': filename
        })
        session.modified = True
        
        # Check if test complete
        if question_num >= len(SPEAKING_PROMPTS):
            return jsonify({"status": "complete", "redirect": url_for('results')})
        else:
            return jsonify({"status": "next", "redirect": url_for('question', num=question_num + 1)})
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/results")
def results():
    if 'student_name' not in session or 'question_data' not in session:
        return redirect(url_for('index'))
    
    question_data = session['question_data']
    
    # Calculate totals
    total_fluency = sum(q['fluency'] for q in question_data) / len(question_data)
    total_pronunciation = sum(q['pronunciation'] for q in question_data) / len(question_data)
    total_score = sum(q['score'] for q in question_data) / len(question_data)
    
    # Save to database
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO student_tests (student_name, total_fluency, total_pronunciation, total_score, overall_feedback)
        VALUES (?, ?, ?, ?, ?)
    ''', (session['student_name'], total_fluency, total_pronunciation, total_score, "Test completed"))
    
    test_id = cursor.lastrowid
    
    for q in question_data:
        cursor.execute('''
            INSERT INTO question_scores (test_id, question_number, prompt_text, fluency, pronunciation, score, feedback, audio_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (test_id, q['question_num'], q['prompt'], q['fluency'], q['pronunciation'], q['score'], q['feedback'], q['audio_file']))
    
    conn.commit()
    conn.close()
    
    return render_template("results.html",
                         student_name=session['student_name'],
                         questions=question_data,
                         total_fluency=round(total_fluency, 1),
                         total_pronunciation=round(total_pronunciation, 1),
                         total_score=round(total_score, 1))

@app.route("/scores")
def scores():
    conn = get_db_connection()
    
    tests = conn.execute('''
        SELECT * FROM student_tests 
        ORDER BY total_score DESC, timestamp DESC
    ''').fetchall()
    
    conn.close()
    
    return render_template("scores.html", tests=tests)

@app.route("/test-detail/<int:test_id>")
def test_detail(test_id):
    conn = get_db_connection()
    
    test = conn.execute('SELECT * FROM student_tests WHERE id = ?', (test_id,)).fetchone()
    questions = conn.execute('SELECT * FROM question_scores WHERE test_id = ? ORDER BY question_number', (test_id,)).fetchall()
    
    conn.close()
    
    if not test:
        flash("Test not found", "error")
        return redirect(url_for('scores'))
    
    return render_template("test_detail.html", test=test, questions=questions)

if __name__ == "__main__":
    print("🚀 Starting Speaking AI Server...")
    print(f"📂 Audio folder: {AUDIO_FOLDER}")
    print(f"💾 Database: {DATABASE}")
    app.run(debug=True, host='0.0.0.0', port=5000)
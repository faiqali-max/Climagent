# Climagent

**Climagent** is an AI-powered environmental and climate intelligence platform. Built with FastAPI and an agentic workflow architecture, it leverages real-time environmental APIs, geospatial datasets, and memory-backed conversational agents to provide localized heat-risk assessments and climate insights.

---

## 🌟 Key Features

* **Multi-Agent Intelligence System:** Autonomous agents processing real-time climate telemetry, localized heat metrics, and user intent.
* **FortyGuard Integration:** Real-time data collection and heat-risk analytics across monitored geographic regions.
* **Dual Database Architecture (SQLite & Supabase):** 
  * **Local Dev:** Zero-config SQLite database (`climagent.db`) for fast, local execution.
  * **Production:** Seamless cloud persistence via Supabase Postgres for user profiles, credit tracking, and long-term memory.
* **Serverless-Ready Architecture:** Includes dynamic environment-aware routing, external cron endpoints (`/api/monitors/run-due`), and Supabase Storage support designed for deployment on platforms like Vercel.
* **Automated Opt-In Workflows:** Built-in mechanisms to handle data requests and regional monitoring permissions.

---

## 🛠️ Tech Stack

* **Backend:** Python 3.10+, FastAPI, Uvicorn, LangChain, SQLAlchemy
* **Frontend:** HTML5, CSS3, JavaScript (Single Page Architecture)
* **Database & Storage:** SQLite (Local) / Supabase Postgres & Storage (Production)
* **LLM Integration:** Google Gemini API
* **Deployment:** Vercel-ready with `vercel.json` and entry point declarations

---

## 🚀 Getting Started

### 1. Prerequisites
* Python 3.10 or higher
* Git

### 2. Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/faiqali-max/Climagent.git](https://github.com/faiqali-max/Climagent.git)
   cd Climagent

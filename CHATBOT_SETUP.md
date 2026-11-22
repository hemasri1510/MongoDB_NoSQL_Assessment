# Floating Threat Intelligence Chatbot Integration

The original Chapter 07 Streamlit dashboard and the original Threat Intelligence chatbot UI were preserved. The website now includes a floating launcher at the bottom-right, which calls the existing FastAPI `/chat` endpoint.

## 1. Install website dependencies

```bash
pip install -r requirements.txt
```

## 2. Start the chatbot backend (terminal 1)

```bash
cd chatbot_backend
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The backend uses Ollama by default. Make sure Ollama is running and the configured model is available (default: `llama3.1:8b`). MongoDB is optional for answering; chat history is saved when MongoDB is reachable.

## 3. Start the website (terminal 2, from this folder)

```bash
streamlit run dashboard.py
```

Open the Streamlit website and click the 💬 button in the bottom-right corner.

## Configuration

The widget defaults to `http://localhost:8000`. If the API runs on another host, edit the `api` value in `floating_chatbot.py` (the `const api=...` line) to the reachable API base URL. The API enables CORS for local development. For production, restrict `allow_origins` in `chatbot_backend/main.py` to your website's domain and use HTTPS.

## Notes

- The chatbot widget is an added floating component; existing dashboard sections, styling, navigation and chatbot's original Streamlit interface are not redesigned.
- Do not publish `.env` files containing real credentials. `.env.example` contains development defaults only.

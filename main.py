import os

from dotenv import load_dotenv

load_dotenv(os.getenv("JOBAGENTS_ENV_FILE", ".env"))

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8000")), debug=False)

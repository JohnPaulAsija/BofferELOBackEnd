from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from initialize import create_client
import pandas as pd

app = FastAPI()

# Global dataframes for matches data
df_null = None
df_confirmed = None

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    """Simple endpoint that returns a JSON response"""
    return {"message": "Hello from API!"}

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "ok"}

@app.get("/user/{jwt}")
def retrieve_user(jwt: str):
    client = create_client()
    user = client.auth.get_user(jwt)
    return {"user": user}

def poll_and_save_matches():
    """Poll the server and save the matches data"""
    client = create_client()
    data = client.from_("Matches").select("*").execute()
    df = pd.DataFrame(data.data)
    return df

@app.get("/data/Matches")
def fetch_Matches():
    """Return filtered dataframes of matches data"""
    return {
        "null_confirmed": df_null.to_dict(orient="records"),
        "confirmed": df_confirmed.to_dict(orient="records")
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

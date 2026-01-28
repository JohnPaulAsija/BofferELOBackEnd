import supabase
import os
from dotenv import load_dotenv
from initialize import create_client
from dbInteractions import fetch_data, retreive_user
from api import app, poll_and_save_matches
import api
import uvicorn

load_dotenv()

def main():    
    print("Hello from apitest!")
    matches = poll_and_save_matches()
    # Create two dataframes: one with NULL confirmedAt, one with non-NULL
    api.df_null = matches[matches["confirmedAt"].isna()]
    api.df_confirmed = matches[matches["confirmedAt"].notna()]

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
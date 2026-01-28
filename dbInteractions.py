import supabase

def fetch_data(client: supabase.Client, table_name: str):
    response = client.table(table_name).select("*").execute()
    return response.data

def retreive_user(jwt):
    response = supabase.auth.api.get_user(jwt)
    return response

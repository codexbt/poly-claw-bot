import requests

token_id = (
    "89878270224547879475144591131717922086770537075946018067980764329502131235560"
)
resp = requests.get(
    "https://clob.polymarket.com/book", params={"token_id": token_id}, timeout=10
)
print("Status:", resp.status_code)
print("Response:", resp.text[:2000])

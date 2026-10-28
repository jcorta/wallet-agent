"""Terminal client: log in against the idp, then chat with the agent service."""

import argparse
import getpass
import os
import sys

import httpx


def login(idp_url: str, username: str, password: str) -> str:
    response = httpx.post(
        f"{idp_url}/login", json={"username": username, "password": password}, timeout=10
    )
    if response.status_code != 200:
        sys.exit("Login failed")
    return response.json()["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idp-url", default=os.environ.get("IDP_URL", "http://localhost:8001"))
    parser.add_argument("--agent-url", default=os.environ.get("AGENT_URL", "http://localhost:8003"))
    parser.add_argument("--username")
    parser.add_argument("--password")
    args = parser.parse_args()

    username = args.username or input("Username: ")
    password = args.password or getpass.getpass("Password: ")
    headers = {"Authorization": f"Bearer {login(args.idp_url, username, password)}"}
    thread_id: str | None = None

    with httpx.Client(base_url=args.agent_url, headers=headers, timeout=120) as client:
        while True:
            try:
                message = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not message:
                continue
            response = client.post("/chat", json={"message": message, "thread_id": thread_id})
            if response.status_code != 200:
                print(f"error {response.status_code}: {response.json()['error']['message']}")
                continue
            body = response.json()
            thread_id = body["thread_id"]
            print(f"agent> {body['reply']}")


if __name__ == "__main__":
    main()

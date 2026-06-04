"""
ingest_events.py — Push events from a JSONL file into the API in batches.
Usage: python3 pipeline/ingest_events.py --file events/events.jsonl --api http://localhost:8000
"""
import json
import argparse
import urllib.request
import urllib.error


def ingest(file_path: str, api_url: str, batch_size: int = 500):
    with open(file_path) as f:
        lines = [l.strip() for l in f if l.strip()]

    total = len(lines)
    accepted = rejected = duplicate = 0

    for i in range(0, total, batch_size):
        batch = [json.loads(l) for l in lines[i:i + batch_size]]
        payload = json.dumps({"events": batch}).encode()
        req = urllib.request.Request(
            f"{api_url}/events/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                accepted  += data.get("accepted", 0)
                rejected  += data.get("rejected", 0)
                duplicate += data.get("duplicate", 0)
                print(f"[ingest] batch {i//batch_size + 1}: "
                      f"accepted={data.get('accepted')} dup={data.get('duplicate')} "
                      f"rejected={data.get('rejected')}")
        except urllib.error.HTTPError as e:
            print(f"[ingest] HTTP {e.code}: {e.read().decode()[:200]}")
        except Exception as e:
            print(f"[ingest] Error: {e}")

    print(f"\n[ingest] Done — total={total} accepted={accepted} "
          f"duplicate={duplicate} rejected={rejected}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file",       required=True, help="Path to events.jsonl")
    ap.add_argument("--api",        default="http://localhost:8000")
    ap.add_argument("--batch-size", type=int, default=500)
    args = ap.parse_args()
    ingest(args.file, args.api, args.batch_size)

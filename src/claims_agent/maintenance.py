"""Run explicitly from a daily operator job; no public maintenance endpoint."""

import argparse
import os
from .store import ClaimStore


def main():
    parser = argparse.ArgumentParser(description="Remove expired encrypted records")
    parser.add_argument("command", choices=["purge-expired"])
    parser.parse_args()
    directory = os.environ["CLAIMS_DATA_DIR"]
    key = os.environ["DATA_ENCRYPTION_KEY"]
    count = ClaimStore(directory, key).purge_expired()
    print(f"Purged {count} expired records")


if __name__ == "__main__":
    main()

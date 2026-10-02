"""Regenerate docs/api-samples/*.json from a throw-away, seeded server (no network, no real data touched).

    venv/bin/python -m tools.gen_api_samples

The files are the censored example responses of the client contract (tokens and signed URL values redacted);
tests/test_client_contract.py fails when they drift from what the server returns.
"""
import os
import sys

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SERVER, "tests"))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB, must precede any `app` import


def main() -> None:
    import _contract_seed as seed

    with seed.seeded_client() as client:
        samples = seed.build_samples(client)
    os.makedirs(seed.SAMPLE_DIR, exist_ok=True)
    for name, body in samples.items():
        with open(os.path.join(seed.SAMPLE_DIR, name), "w", encoding="utf-8") as fh:
            fh.write(seed.dump(body))
    print("wrote %d samples to %s" % (len(samples), seed.SAMPLE_DIR))


if __name__ == "__main__":
    main()

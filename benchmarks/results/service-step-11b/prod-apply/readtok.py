#!/usr/bin/env python3
# Step 11B gate helper: print the gateway auth token from a cloned config file.
# Used ONLY to populate the CLI process environment at runtime. The token value
# is never echoed to the transcript, never logged, and never written to a file.
import json
import sys

cfg = json.load(open(sys.argv[1]))
print(cfg["gateway"]["auth"]["token"], end="")
